"""Regression: byte limits must hold for a chunked upload too.

`enforce_request_size` reads `Content-Length` and, finding none, returns without a verdict —
which is correct as far as it goes, but it was the *only* pre-parse guard. A publisher using
`Transfer-Encoding: chunked` (curl does this for any streamed body, and so does every HTTP
client given a generator) therefore had no ceiling at all: Starlette parsed the whole
multipart envelope first, and the route then called `await upload.read()` with no argument,
materialising the entire part in memory before a single limit was consulted.

Two independent guards are needed, and both are tested here:

* a body-size ceiling on the ASGI receive channel, which counts the bytes that actually
  arrive rather than the bytes the caller *claims* will arrive;
* bounded, chunk-at-a-time reads of each part, so nothing over the configured limit is ever
  concatenated into a single object.
"""

from __future__ import annotations

import pytest

from tests.conftest import API_TOKEN

BOUNDARY = "----ArtifactPublisherSmokeBoundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def multipart_body(
    fields: dict[str, str],
    content: bytes | None = None,
    assets: list[tuple[str, bytes]] | None = None,
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode()
            + b"\r\n"
        )
    if content is not None:
        chunks.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="content";'
            f' filename="s.md"\r\nContent-Type: text/plain\r\n\r\n'.encode()
            + content
            + b"\r\n"
        )
    for filename, blob in assets or []:
        chunks.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="assets";'
            f' filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + blob
            + b"\r\n"
        )
    chunks.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(chunks)


def stream(body: bytes, chunk: int = 8192):
    """A generator body makes httpx use `Transfer-Encoding: chunked` and omit Content-Length."""
    for start in range(0, len(body), chunk):
        yield body[start : start + chunk]


def chunked_publish(client, body: bytes, token: str | None = API_TOKEN):
    headers = {"Content-Type": CONTENT_TYPE}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/artifacts", headers=headers, content=stream(body))


def test_the_helper_really_sends_a_chunked_request_with_no_content_length(client):
    """Guard the premise: if httpx started sending Content-Length these tests would lie."""
    seen: dict[str, str] = {}

    original = client.request

    def spy(method, url, **kwargs):  # type: ignore[no-untyped-def]
        response = original(method, url, **kwargs)
        seen.update({k.lower(): v for k, v in response.request.headers.items()})
        return response

    client.request = spy  # type: ignore[method-assign]
    try:
        chunked_publish(client, multipart_body({"title": "T", "format": "markdown"}, b"# hi\n"))
    finally:
        client.request = original  # type: ignore[method-assign]

    assert "content-length" not in seen, seen
    assert seen.get("transfer-encoding") == "chunked", seen


def test_a_valid_chunked_publish_still_works(client):
    body = multipart_body({"title": "Отчёт", "format": "markdown"}, "# Привет\n".encode())

    response = chunked_publish(client, body)

    assert response.status_code == 201, response.text


def test_oversized_content_is_rejected_without_a_content_length(make_client):
    client = make_client(max_content_bytes=1024)
    body = multipart_body({"title": "T", "format": "markdown"}, b"x" * 4096)

    response = chunked_publish(client, body)

    assert response.status_code == 413, response.text
    assert list(client.app.state.store.artifacts_dir.iterdir()) == []
    assert list(client.app.state.store.tmp_dir.iterdir()) == []


def test_oversized_assets_are_rejected_without_a_content_length(make_client):
    client = make_client(max_asset_bytes=2048)
    body = multipart_body(
        {"title": "T", "format": "markdown"},
        b"# hi\n",
        assets=[("a.png", b"x" * 1500), ("b.png", b"y" * 1500)],
    )

    response = chunked_publish(client, body)

    assert response.status_code == 413, response.text
    assert list(client.app.state.store.artifacts_dir.iterdir()) == []


def test_a_lying_content_length_does_not_buy_a_bigger_upload(make_client):
    """A small declared length must not license a large actual body."""
    client = make_client(max_content_bytes=1024)
    body = multipart_body({"title": "T", "format": "markdown"}, b"x" * 4096)

    response = client.post(
        "/api/artifacts",
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": CONTENT_TYPE,
            "Content-Length": "10",
        },
        content=body,
    )

    assert response.status_code in (400, 413, 422), response.status_code
    assert list(client.app.state.store.artifacts_dir.iterdir()) == []


def test_a_body_past_the_envelope_ceiling_is_cut_off_mid_stream(make_client):
    """The receive channel itself stops counting past the ceiling — nothing is buffered."""
    from artifact_relay.validation import max_request_bytes

    client = make_client(max_content_bytes=1024, max_asset_bytes=1024)
    ceiling = max_request_bytes(client.app.state.settings)
    body = multipart_body({"title": "T", "format": "markdown"}, b"x" * (ceiling * 2))

    response = chunked_publish(client, body)

    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "too large" in response.json()["detail"].lower()


def test_an_unauthenticated_oversized_body_is_never_read(make_client):
    client = make_client(max_content_bytes=1024)
    body = multipart_body({"title": "T", "format": "markdown"}, b"x" * 4_000_000)

    response = chunked_publish(client, body, token=None)

    assert response.status_code in (401, 413), response.text
    assert list(client.app.state.store.artifacts_dir.iterdir()) == []


# --- the bounded read itself -------------------------------------------------


@pytest.mark.anyio
async def test_read_bounded_returns_the_payload_when_it_fits():
    from artifact_relay.validation import read_bounded

    upload = _FakeUpload(b"a" * 100)

    assert await read_bounded(upload, 100) == b"a" * 100


@pytest.mark.anyio
async def test_read_bounded_rejects_one_byte_past_the_limit():
    from fastapi import HTTPException

    from artifact_relay.validation import read_bounded

    upload = _FakeUpload(b"a" * 101)

    with pytest.raises(HTTPException) as raised:
        await read_bounded(upload, 100)

    assert raised.value.status_code == 413


@pytest.mark.anyio
async def test_read_bounded_never_asks_for_the_whole_part_at_once():
    """`await upload.read()` with no size is the bug; every read must be explicitly sized."""
    from fastapi import HTTPException

    from artifact_relay.validation import read_bounded

    upload = _FakeUpload(b"a" * 10_000_000)

    with pytest.raises(HTTPException):
        await read_bounded(upload, 1024)

    assert upload.requested, "nothing was read"
    assert all(size is not None and size > 0 for size in upload.requested), upload.requested
    assert max(upload.requested) <= 1024 * 1024
    assert upload.delivered <= 1024 * 1024 + 1024, (
        f"{upload.delivered} bytes were pulled into memory for a 1024-byte limit"
    )


@pytest.mark.anyio
async def test_read_bounded_stops_pulling_bytes_as_soon_as_the_limit_is_passed():
    from fastapi import HTTPException

    from artifact_relay.validation import read_bounded

    upload = _FakeUpload(b"a" * 10_000_000)

    with pytest.raises(HTTPException):
        await read_bounded(upload, 16)

    assert upload.delivered < 10_000_000, "the whole part was drained despite a 16-byte limit"


class _FakeUpload:
    """Minimal stand-in for `UploadFile` that records how it was read."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.requested: list[int | None] = []
        self.delivered = 0

    async def read(self, size: int = -1) -> bytes:
        self.requested.append(size if size >= 0 else None)
        if size < 0:
            chunk = self._payload[self._offset :]
        else:
            chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        self.delivered += len(chunk)
        return chunk


@pytest.fixture
def anyio_backend():
    return "asyncio"
