"""Shared fixtures.

The Argon2 parameters used here are deliberately *weak* so the suite stays fast: the
parameters are encoded in the hash string itself, so verification is cheap too. Production
hashes are produced by `scripts/hash_password.py` with library defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from argon2 import PasswordHasher

VIEW_PASSWORD = "correct horse battery staple"
API_TOKEN = "test-token-abcdefghijklmnop"
BASE_URL = "https://artifacts.example.test"

_TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an operator's exported service configuration out of the test suite."""
    from artifact_relay.config import Settings

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def settings(tmp_path: Path):  # type: ignore[no-untyped-def]
    from artifact_relay.config import Settings

    return Settings(  # type: ignore[call-arg,arg-type]
        _env_file=None,
        data_dir=tmp_path / "data",
        base_url=BASE_URL,
        share_links_enabled=True,
        artifact_api_token=API_TOKEN,
        view_password_hash=_TEST_HASHER.hash(VIEW_PASSWORD),
        session_secret_key="s" * 48,
    )


@pytest.fixture
def client(settings) -> Iterator:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from artifact_relay.app import create_app

    with TestClient(create_app(settings), base_url=BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def publish(client):  # type: ignore[no-untyped-def]
    """Publish an artifact through the API; returns the raw response."""

    def _publish(
        title: str = "Отчёт о нагрузочном тесте",
        fmt: str = "markdown",
        content: bytes = "# Заголовок\n\nТекст.\n".encode(),
        *,
        token: str | None = API_TOKEN,
        assets: list[tuple[str, bytes]] | None = None,
        filename: str = "source.md",
        **extra: object,
    ):
        data: dict[str, object] = {"title": title, "format": fmt}
        data.update(extra)
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("content", (filename, content, "text/plain"))
        ]
        for asset_name, blob in assets or []:
            files.append(("assets", (asset_name, blob, "application/octet-stream")))
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return client.post("/api/artifacts", headers=headers, data=data, files=files)

    return _publish


@pytest.fixture
def make_client(tmp_path):  # type: ignore[no-untyped-def]
    """Build a TestClient whose Settings differ from the defaults."""
    from fastapi.testclient import TestClient

    from artifact_relay.app import create_app
    from artifact_relay.config import Settings

    created = []

    def _make(**overrides: object):  # type: ignore[no-untyped-def]
        base: dict[str, object] = {
            "data_dir": tmp_path / f"data{len(created)}",
            "base_url": BASE_URL,
            "share_links_enabled": True,
            "artifact_api_token": API_TOKEN,
            "view_password_hash": _TEST_HASHER.hash(VIEW_PASSWORD),
            "session_secret_key": "s" * 48,
        }
        base.update(overrides)
        settings = Settings(_env_file=None, **base)  # type: ignore[arg-type,call-arg]
        client = TestClient(create_app(settings), base_url=str(settings.base_url))
        client.__enter__()
        created.append(client)
        return client

    yield _make
    for client in created:
        client.__exit__(None, None, None)


@pytest.fixture
def logged_in(client):  # type: ignore[no-untyped-def]
    """The shared client, carrying a valid viewer session cookie."""
    response = client.post(
        "/login", data={"password": VIEW_PASSWORD, "next": "/"}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


@pytest.fixture
def expire_artifact(client):  # type: ignore[no-untyped-def]
    """Back-date an artifact's expiry so it is already in the past."""
    from datetime import UTC, datetime, timedelta

    from artifact_relay.db import connect

    def _expire(artifact_id: str, *, seconds_ago: int = 60) -> None:
        store = client.app.state.store
        moment = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
        with connect(store.db_path) as conn:
            conn.execute("UPDATE artifacts SET expires_at = ? WHERE id = ?", (moment, artifact_id))

    return _expire
