from pathlib import Path

from fastapi.testclient import TestClient

from artifact_relay.app import create_app
from artifact_relay.config import Settings
from tests.conftest import _TEST_HASHER, API_TOKEN, BASE_URL, VIEW_PASSWORD

BODY = "ТЕЛО-ПЕРЕЖИВШЕЕ-ПЕРЕЗАПУСК"


def build_settings(data_dir: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "data_dir": data_dir,
        "base_url": BASE_URL,
        "artifact_api_token": API_TOKEN,
        "view_password_hash": _TEST_HASHER.hash(VIEW_PASSWORD),
        "session_secret_key": "s" * 48,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def publish_into(client: TestClient) -> str:
    response = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "Пережившее перезапуск", "format": "markdown"},
        files=[
            ("content", ("s.md", f"# T\n\n{BODY}\n".encode(), "text/plain")),
            ("assets", ("chart.png", b"\x89PNG-bytes", "application/octet-stream")),
        ],
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_artifacts_and_sessions_survive_a_restart(tmp_path):
    data_dir = tmp_path / "data"

    with TestClient(create_app(build_settings(data_dir)), base_url=BASE_URL) as first:
        artifact_id = publish_into(first)
        first.post("/login", data={"password": VIEW_PASSWORD, "next": "/"}, follow_redirects=False)
        cookie = first.cookies["ap_session"]
        assert BODY in first.get(f"/a/{artifact_id}").text

    # A brand new process object over the very same directory.
    with TestClient(create_app(build_settings(data_dir)), base_url=BASE_URL) as second:
        second.cookies.set("ap_session", cookie, domain="artifacts.example.test")

        page = second.get(f"/a/{artifact_id}")
        assert page.status_code == 200
        assert BODY in page.text, "artifact body did not survive the restart"

        asset = second.get(f"/a/{artifact_id}/assets/chart.png")
        assert asset.status_code == 200
        assert asset.content == b"\x89PNG-bytes"

        source = second.get(f"/a/{artifact_id}/source")
        assert source.status_code == 200
        assert BODY in source.text


def test_rotating_the_session_secret_invalidates_existing_sessions(tmp_path):
    data_dir = tmp_path / "data"

    with TestClient(create_app(build_settings(data_dir)), base_url=BASE_URL) as first:
        artifact_id = publish_into(first)
        first.post("/login", data={"password": VIEW_PASSWORD, "next": "/"}, follow_redirects=False)
        cookie = first.cookies["ap_session"]

    rotated = build_settings(data_dir, session_secret_key="r" * 48)
    with TestClient(create_app(rotated), base_url=BASE_URL) as second:
        second.cookies.set("ap_session", cookie, domain="artifacts.example.test")

        page = second.get(f"/a/{artifact_id}")

        assert page.status_code == 200
        assert BODY not in page.text, "a forged/stale session was accepted"
        assert 'type="password"' in page.text


def test_a_tampered_session_cookie_is_rejected(client, publish, logged_in):
    artifact_id = publish(content=f"# T\n\n{BODY}\n".encode()).json()["id"]
    good = client.cookies["ap_session"]

    client.cookies.set("ap_session", good[:-3] + "AAA", domain="artifacts.example.test")

    page = client.get(f"/a/{artifact_id}")

    assert BODY not in page.text
    assert 'type="password"' in page.text


def test_the_data_directory_is_created_on_first_start(tmp_path):
    data_dir = tmp_path / "does" / "not" / "exist" / "yet"

    with TestClient(create_app(build_settings(data_dir)), base_url=BASE_URL) as client:
        assert client.get("/api/health").status_code == 200

    assert (data_dir / "artifacts.db").is_file()
    assert (data_dir / "artifacts").is_dir()
    assert (data_dir / "tmp").is_dir()
