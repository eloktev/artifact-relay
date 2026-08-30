from http.cookies import SimpleCookie

from tests.conftest import VIEW_PASSWORD


def parse_set_cookie(response, name: str) -> SimpleCookie:
    raw = [v for v in response.headers.get_list("set-cookie") if v.startswith(f"{name}=")]
    assert raw, f"no Set-Cookie for {name}: {response.headers.get_list('set-cookie')}"
    jar = SimpleCookie()
    jar.load(raw[0])
    return jar


def test_login_page_renders_a_password_form(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'type="password"' in response.text
    assert 'method="post"' in response.text.lower()


def test_successful_login_sets_a_hardened_session_cookie(client, settings):
    response = client.post(
        "/login",
        data={"password": VIEW_PASSWORD, "next": "/a/abc123"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/a/abc123"

    jar = parse_set_cookie(response, settings.session_cookie_name)
    morsel = jar[settings.session_cookie_name]
    assert morsel.value, "cookie must carry a signed value"
    assert morsel["httponly"] is True
    assert morsel["secure"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert int(morsel["max-age"]) == settings.session_ttl_days * 86400

    # The password must never appear in the cookie, signed or not.
    assert VIEW_PASSWORD not in morsel.value


def test_wrong_password_is_rejected_without_a_cookie(client, settings):
    response = client.post(
        "/login", data={"password": "не тот пароль", "next": "/"}, follow_redirects=False
    )

    assert response.status_code == 401
    assert not [
        v
        for v in response.headers.get_list("set-cookie")
        if v.startswith(f"{settings.session_cookie_name}=") and "Max-Age=0" not in v
    ]
    assert "не тот пароль" not in response.text


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "hostile_next",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "/\\evil.example/steal",
        "\\\\evil.example",
        "http://evil.example",
        "javascript:alert(1)",
        "/a/x\r\nSet-Cookie: pwned=1",
        "",
    ],
)
def test_login_never_redirects_off_site(client, hostile_next):
    response = client.post(
        "/login",
        data={"password": VIEW_PASSWORD, "next": hostile_next},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location == "/", location
    assert "evil.example" not in location


def test_login_page_does_not_reflect_hostile_next_into_the_form(client):
    response = client.get("/login", params={"next": "https://evil.example/x"})

    assert response.status_code == 200
    assert "evil.example" not in response.text


def test_logout_clears_the_session_cookie(client, settings):
    client.post("/login", data={"password": VIEW_PASSWORD, "next": "/"}, follow_redirects=False)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    jar = parse_set_cookie(response, settings.session_cookie_name)
    morsel = jar[settings.session_cookie_name]
    assert morsel.value == ""
    assert int(morsel["max-age"]) == 0
    assert morsel["httponly"] is True


@pytest.mark.parametrize(
    "broken_hash",
    ["", "not-a-hash", "$argon2id$broken", "$2b$12$abcdefghijklmnopqrstuv"],
)
def test_a_malformed_password_hash_never_authenticates_anyone(broken_hash):
    from artifact_relay.security import verify_view_password

    assert verify_view_password("любой пароль", broken_hash) is False
    assert verify_view_password("", broken_hash) is False


@pytest.mark.parametrize(
    "hostile",
    ["/a/x\rSet-Cookie: p=1", "/a/x\nLocation: https://evil.example", "/a/\x01x", "/a/x\x00y"],
)
def test_control_characters_in_next_are_refused(hostile):
    from artifact_relay.security import safe_next_path

    assert safe_next_path(hostile) == "/"


def test_an_expired_signature_is_rejected():
    from artifact_relay.security import SessionSigner

    signer = SessionSigner("k" * 48, max_age_seconds=0)
    token = signer.issue()

    import time

    time.sleep(1.1)
    assert signer.verify(token) is False


def test_a_token_signed_with_another_key_is_rejected():
    from artifact_relay.security import SessionSigner

    mine = SessionSigner("k" * 48, max_age_seconds=3600)
    theirs = SessionSigner("j" * 48, max_age_seconds=3600)

    assert mine.verify(theirs.issue()) is False
    assert mine.verify(mine.issue()) is True
    assert mine.verify(None) is False
