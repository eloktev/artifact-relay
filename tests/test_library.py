from __future__ import annotations

from artifact_relay.db import connect

VIEW_PASSWORD = "correct horse battery staple"


def test_publish_persists_session_and_topic_metadata(publish, client):  # type: ignore[no-untyped-def]
    response = publish(
        session_id="session-123",
        session_title="Развитие артефактов",
        platform="telegram",
        chat_name="ЦУП",
        topic_id="5325",
        topic_name="Эволюция",
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["session_id"] == "session-123"
    assert payload["session_title"] == "Развитие артефактов"
    assert payload["topic_name"] == "Эволюция"

    artifact = client.app.state.store.get(payload["id"])
    assert artifact is not None
    assert artifact.session_id == "session-123"
    assert artifact.session_title == "Развитие артефактов"
    assert artifact.platform == "telegram"
    assert artifact.chat_name == "ЦУП"
    assert artifact.topic_id == "5325"
    assert artifact.topic_name == "Эволюция"
    assert artifact.favorite is False


def test_database_migrates_existing_artifacts_without_metadata(tmp_path):  # type: ignore[no-untyped-def]
    from artifact_relay.db import init_db

    db_path = tmp_path / "artifacts.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT,
                format TEXT NOT NULL, source_filename TEXT NOT NULL,
                content_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT
            );
            INSERT INTO artifacts VALUES (
                'abcdefghijklmnopqrstuv', 'Legacy', NULL, 'markdown',
                'source.md', 1, '2026-08-29T00:00:00+00:00', NULL
            );
            """
        )

    init_db(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT favorite, session_id, topic_name FROM artifacts WHERE title = 'Legacy'"
        ).fetchone()
    assert row is not None
    assert row["favorite"] == 0
    assert row["session_id"] is None
    assert row["topic_name"] is None


def test_trusted_agent_can_backfill_provenance(client, publish, settings):  # type: ignore[no-untyped-def]
    artifact_id = publish(title="Исторический артефакт").json()["id"]
    token = settings.artifact_api_token.get_secret_value()

    response = client.patch(
        f"/api/artifacts/{artifact_id}/provenance",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "historical-session",
            "session_title": "Историческая сессия",
            "platform": "telegram",
            "chat_name": "ЦУП",
            "topic_id": "5325",
            "topic_name": "Эволюция",
        },
    )

    assert response.status_code == 200
    assert response.json()["session_title"] == "Историческая сессия"
    artifact = client.app.state.store.get(artifact_id)
    assert artifact is not None
    assert artifact.session_id == "historical-session"
    assert artifact.topic_name == "Эволюция"


def test_logged_in_home_lists_favorites_first_with_provenance(logged_in, publish):  # type: ignore[no-untyped-def]
    first = publish(
        title="Обычный артефакт",
        session_title="Обычная сессия",
        topic_id="99",
        topic_name="Прочее",
    ).json()
    favorite = publish(
        title="Важный артефакт",
        session_title="Сессия про инструменты",
        topic_id="5325",
        topic_name="Эволюция",
    ).json()
    logged_in.post(f"/a/{favorite['id']}/favorite", follow_redirects=False)

    response = logged_in.get("/")

    assert response.status_code == 200
    assert "Избранное" in response.text
    assert "Важный артефакт" in response.text
    assert "Сессия про инструменты" in response.text
    assert "Эволюция" in response.text
    assert "Все артефакты" in response.text
    assert "Обычный артефакт" in response.text
    assert response.text.index("Важный артефакт") < response.text.index("Все артефакты")
    assert first["id"] in response.text


def test_home_filters_artifacts_by_selected_topic(logged_in, publish):  # type: ignore[no-untyped-def]
    selected = publish(
        title="Artifact Relay roadmap",
        platform="telegram",
        chat_name="ЦУП",
        topic_id="7591",
    ).json()
    other = publish(
        title="Unrelated report",
        platform="telegram",
        chat_name="ЦУП",
        topic_id="745",
    ).json()

    response = logged_in.get(
        "/",
        params={"platform": "telegram", "chat_name": "ЦУП", "topic_id": "7591"},
    )

    assert response.status_code == 200
    assert selected["id"] in response.text
    assert other["id"] not in response.text
    assert "Сбросить фильтр" in response.text


def test_topic_alias_is_human_readable_and_applies_to_future_artifacts(logged_in, publish):  # type: ignore[no-untyped-def]
    publish(
        title="First artifact",
        platform="telegram",
        chat_name="ЦУП",
        topic_id="7591",
    )

    renamed = logged_in.post(
        "/topics/name",
        data={
            "platform": "telegram",
            "chat_name": "ЦУП",
            "topic_id": "7591",
            "topic_name": "Artifact Relay",
        },
        follow_redirects=False,
    )

    assert renamed.status_code == 303
    assert "topic_id=7591" in renamed.headers["location"]

    future = publish(
        title="Future artifact",
        platform="telegram",
        chat_name="ЦУП",
        topic_id="7591",
    ).json()
    response = logged_in.get("/")

    assert future["id"] in response.text
    assert "Artifact Relay" in response.text
    assert "Топик 7591" not in response.text


def test_favorite_requires_viewer_session(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish().json()["id"]

    response = client.post(f"/a/{artifact_id}/favorite", follow_redirects=False)

    assert response.status_code == 403


def test_favorite_toggle_returns_to_artifact_and_updates_store(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish().json()["id"]
    client.post(
        "/login",
        data={"password": VIEW_PASSWORD, "next": f"/a/{artifact_id}"},
        follow_redirects=False,
    )

    enabled = client.post(f"/a/{artifact_id}/favorite", follow_redirects=False)
    assert enabled.status_code == 303
    assert enabled.headers["location"] == f"/a/{artifact_id}"
    assert client.app.state.store.get(artifact_id).favorite is True

    disabled = client.post(f"/a/{artifact_id}/favorite", follow_redirects=False)
    assert disabled.status_code == 303
    assert client.app.state.store.get(artifact_id).favorite is False


def test_home_requires_login(client):  # type: ignore[no-untyped-def]
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
