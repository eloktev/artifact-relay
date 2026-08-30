from datetime import datetime

from tests.conftest import BASE_URL


def test_publish_markdown_returns_absolute_url_and_lifetime(publish):
    response = publish(title="Отчёт", summary="Коротко")

    assert response.status_code == 201, response.text
    body = response.json()

    assert set(body) >= {"id", "url", "title", "summary", "format", "created_at", "expires_at"}
    assert body["title"] == "Отчёт"
    assert body["summary"] == "Коротко"
    assert body["format"] == "markdown"
    assert body["url"] == f"{BASE_URL}/a/{body['id']}"

    created = datetime.fromisoformat(body["created_at"])
    expires = datetime.fromisoformat(body["expires_at"])
    assert created.tzinfo is not None and expires.tzinfo is not None
    assert round((expires - created).total_seconds() / 86400) == 30
