import pytest

from tests.conftest import API_TOKEN

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def raw_publish(client, **fields):
    data = {"title": "T", "format": "markdown"}
    data.update({k: v for k, v in fields.items() if k not in {"content", "assets"}})
    files = [("content", ("s.md", fields.get("content", b"# hi\n"), "text/plain"))]
    for name, blob in fields.get("assets", []):
        files.append(("assets", (name, blob, "application/octet-stream")))
    return client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data=data,
        files=files,
    )


@pytest.mark.parametrize("title", ["", "   ", "\n\t "])
def test_blank_titles_are_rejected(client, title):
    assert raw_publish(client, title=title).status_code == 422


def test_overlong_title_is_rejected(client, settings):
    ok = raw_publish(client, title="я" * settings.max_title_chars)
    too_long = raw_publish(client, title="я" * (settings.max_title_chars + 1))

    assert ok.status_code == 201
    assert too_long.status_code == 422


def test_overlong_summary_is_rejected(client, settings):
    assert raw_publish(client, summary="я" * settings.max_summary_chars).status_code == 201
    assert raw_publish(client, summary="я" * (settings.max_summary_chars + 1)).status_code == 422


def test_oversized_content_is_rejected(make_client):
    client = make_client(max_content_bytes=1024)

    assert raw_publish(client, content=b"x" * 1024).status_code == 201
    response = raw_publish(client, content=b"x" * 1025)
    assert response.status_code == 413, response.text


def test_empty_content_is_rejected(client):
    assert raw_publish(client, content=b"").status_code == 422


def test_non_utf8_content_is_rejected(client):
    response = raw_publish(client, content=b"\xff\xfe\x00invalid")

    assert response.status_code == 422
    assert "utf-8" in response.text.lower()


def test_too_many_assets_are_rejected(make_client):
    client = make_client(max_assets=2)

    assert raw_publish(client, assets=[(f"a{i}.png", PNG) for i in range(2)]).status_code == 201
    response = raw_publish(client, assets=[(f"b{i}.png", PNG) for i in range(3)])
    assert response.status_code == 413, response.text


def test_total_asset_bytes_are_capped(make_client):
    client = make_client(max_asset_bytes=1000)

    assert raw_publish(client, assets=[("a.png", b"x" * 900)]).status_code == 201
    response = raw_publish(client, assets=[("a.png", b"x" * 600), ("b.png", b"y" * 600)])
    assert response.status_code == 413, response.text


@pytest.mark.parametrize("days", [-1, -365, 100000])
def test_out_of_range_expiry_is_rejected(client, days):
    assert raw_publish(client, expires_in_days=days).status_code == 422


def test_zero_days_means_pinned(client):
    body = raw_publish(client, expires_in_days=0).json()

    assert body["expires_at"] is None


def test_explicit_expiry_is_honoured(client):
    from datetime import datetime

    body = raw_publish(client, expires_in_days=1).json()

    delta = datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(body["created_at"])
    assert round(delta.total_seconds() / 86400) == 1


@pytest.mark.parametrize("fmt", ["pdf", "MARKDOWN ", "", "docx", "markdown; html"])
def test_unknown_formats_are_rejected(client, fmt):
    assert raw_publish(client, format=fmt).status_code == 422


def test_content_part_is_required(client):
    response = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "T", "format": "markdown"},
    )

    assert response.status_code == 422


def test_nothing_is_persisted_when_a_limit_is_violated(make_client):
    client = make_client(max_content_bytes=64)

    assert raw_publish(client, content=b"x" * 4096).status_code == 413

    store = client.app.state.store
    assert list(store.artifacts_dir.iterdir()) == []
    assert list(store.tmp_dir.iterdir()) == []
