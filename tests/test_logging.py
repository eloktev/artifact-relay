import io
import json
import logging

from tests.conftest import API_TOKEN, VIEW_PASSWORD

BODY_MARKER = "СЕКРЕТНОЕ-СОДЕРЖИМОЕ-АРТЕФАКТА"


def capture(settings) -> tuple[logging.Handler, io.StringIO]:
    from artifact_relay.logging_setup import build_handler

    stream = io.StringIO()
    handler = build_handler(settings, stream=stream)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return handler, stream


def test_access_logs_are_structured_json(client, settings, publish):
    handler, stream = capture(settings)
    try:
        client.get("/api/health")
    finally:
        logging.getLogger().removeHandler(handler)

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "no log line was emitted for a request"

    records = [json.loads(line) for line in lines]
    access = [r for r in records if r.get("event") == "request"]
    assert access, records

    entry = access[-1]
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/health"
    assert entry["status"] == 200
    assert isinstance(entry["duration_ms"], int | float)
    assert "request_id" in entry


def test_no_secret_ever_reaches_the_log(client, settings, publish, logged_in):
    handler, stream = capture(settings)
    try:
        publish(content=f"# T\n\n{BODY_MARKER}\n".encode())
        client.post("/login", data={"password": VIEW_PASSWORD, "next": "/"})
        artifact_id = publish(content=f"# T\n\n{BODY_MARKER}\n".encode()).json()["id"]
        client.get(f"/a/{artifact_id}")
        client.get(f"/a/{artifact_id}/source")
        client.delete(
            f"/api/artifacts/{artifact_id}",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )
        logging.getLogger("artifact_relay").info(
            "leaky log line", extra={"token": API_TOKEN, "password": VIEW_PASSWORD}
        )
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()

    assert API_TOKEN not in output
    assert VIEW_PASSWORD not in output
    assert settings.session_secret_key.get_secret_value() not in output
    assert settings.view_password_hash.get_secret_value() not in output
    assert BODY_MARKER not in output
    assert "Bearer " not in output
    assert "ap_session=" not in output


def test_exception_tracebacks_are_also_redacted(settings):
    handler, stream = capture(settings)
    logger = logging.getLogger("artifact_relay.test")
    try:
        try:
            raise ValueError(f"boom with {API_TOKEN}")
        except ValueError:
            logger.exception("handler failed")
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    assert "handler failed" in output
    assert API_TOKEN not in output
    assert "***" in output


def test_cookie_and_authorization_headers_are_never_logged(client, settings):
    handler, stream = capture(settings)
    try:
        client.get(
            "/api/health",
            headers={"Authorization": f"Bearer {API_TOKEN}", "Cookie": "ap_session=abc123"},
        )
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    assert "abc123" not in output
    assert "authorization" not in output.lower()


def test_share_embed_capability_is_redacted_by_access_log_middleware(client, settings):
    handler, stream = capture(settings)
    token = "secret-signed-iframe-token"
    try:
        client.get(f"/s/public-id/embed/{token}/assets/chart.png")
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    records = [json.loads(line) for line in output.splitlines() if line.strip()]
    access = [record for record in records if record.get("event") == "request"]
    assert token not in json.dumps(access)
    assert access[-1]["path"] == "/s/public-id/embed/***/assets/chart.png"
