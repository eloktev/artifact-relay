def test_health_returns_ok_without_secrets(client, settings):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"

    body = response.text
    assert settings.artifact_api_token.get_secret_value() not in body
    assert settings.session_secret_key.get_secret_value() not in body
    assert settings.view_password_hash.get_secret_value() not in body
