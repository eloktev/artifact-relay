import hmac

import pytest

from tests.conftest import API_TOKEN


def publish(client, token=None, **overrides):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    data = {"title": "Отчёт", "format": "markdown"}
    data.update(overrides)
    return client.post(
        "/api/artifacts",
        headers=headers,
        data=data,
        files={"content": ("report.md", "# Заголовок\n".encode(), "text/markdown")},
    )


def test_publish_without_authorization_header_is_401(client):
    response = publish(client)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_publish_with_wrong_token_is_401(client):
    assert publish(client, token="totally-wrong-token-value").status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        API_TOKEN[:-1],  # correct prefix, one char short
        API_TOKEN + "x",  # correct prefix, one char long
        API_TOKEN[:-1] + "X",  # differs only in the final character
        API_TOKEN.upper(),  # case must matter
        "",  # empty
    ],
)
def test_publish_rejects_near_miss_tokens(client, token):
    assert publish(client, token=token).status_code == 401


def test_bearer_comparison_is_constant_time(monkeypatch):
    """The token check must go through hmac.compare_digest, never ``==``."""
    from artifact_relay import security

    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)

    assert security.verify_bearer_token("Bearer abc", "abc") is True
    assert security.verify_bearer_token("Bearer abd", "abc") is False
    assert len(calls) == 2


@pytest.mark.parametrize(
    "header",
    [None, "", "abc", "Basic abc", "Bearer", "bearerabc", "Token abc"],
)
def test_malformed_authorization_headers_are_rejected(header):
    from artifact_relay.security import verify_bearer_token

    assert verify_bearer_token(header, "abc") is False


def test_bearer_scheme_is_case_insensitive():
    from artifact_relay.security import verify_bearer_token

    assert verify_bearer_token("bearer abc", "abc") is True


def test_publish_with_valid_token_passes_authentication(client):
    """The correct token must get past the auth layer (whatever happens afterwards)."""
    response = publish(client, token=API_TOKEN)
    assert response.status_code not in (401, 404, 405), response.text
