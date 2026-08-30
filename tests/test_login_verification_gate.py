"""Regression: concurrent Argon2 verifications must be globally bounded.

Argon2id is memory-hard *by design* — the library defaults allocate 64 MiB per verification.
`POST /login` is a sync endpoint, so Starlette runs it on the anyio worker thread pool (40
threads by default). Without a bound, ~40 simultaneous login attempts ask the allocator for
~2.5 GiB at once and the container is OOM-killed. That is a pre-auth denial of service that
costs the attacker nothing but a handful of TCP connections, and no *per-client* limit fixes
it: the whole point is that the requests come from many addresses.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.conftest import VIEW_PASSWORD


def test_gate_admits_only_up_to_its_bound():
    from artifact_relay.security import PasswordVerificationGate

    gate = PasswordVerificationGate(2)

    with gate.admit() as first, gate.admit() as second:
        assert first is True
        assert second is True
        with gate.admit() as third:
            assert third is False, "the gate admitted more work than its bound allows"

    with gate.admit() as again:
        assert again is True, "slots must be returned when the block exits"


def test_gate_releases_its_slot_even_when_the_body_raises():
    from artifact_relay.security import PasswordVerificationGate

    gate = PasswordVerificationGate(1)

    with pytest.raises(RuntimeError), gate.admit() as admitted:
        assert admitted is True
        raise RuntimeError("verification blew up")

    with gate.admit() as admitted:
        assert admitted is True


def test_a_refused_slot_is_never_released_twice():
    """A rejected caller must not hand back a slot it never held."""
    from artifact_relay.security import PasswordVerificationGate

    gate = PasswordVerificationGate(1)

    with gate.admit() as held:
        assert held is True
        for _ in range(5):
            with gate.admit() as refused:
                assert refused is False

    assert gate.available == 1


def test_login_is_refused_with_503_while_every_slot_is_taken(make_client):
    client = make_client(login_max_concurrent_verifications=1)
    gate = client.app.state.verification_gate

    with gate.admit() as held:
        assert held is True
        response = client.post(
            "/login", data={"password": VIEW_PASSWORD, "next": "/"}, follow_redirects=False
        )

    assert response.status_code == 503, response.text
    assert response.headers["retry-after"] == "1"
    assert "ap_session" not in response.headers.get("set-cookie", "")


def test_a_saturated_login_does_not_count_against_the_client_throttle(make_client):
    """Being turned away by the gate is not a failed password attempt."""
    client = make_client(login_max_concurrent_verifications=1, login_max_attempts=2)
    gate = client.app.state.verification_gate

    with gate.admit():
        for _ in range(5):
            assert client.post("/login", data={"password": "wrong", "next": "/"}).status_code == 503

    assert len(client.app.state.login_limiter) == 0, "a refused slot was recorded as a failure"
    # The throttle budget is intact, so a genuine attempt still gets through afterwards.
    assert client.post("/login", data={"password": "wrong", "next": "/"}).status_code == 401


def test_concurrent_logins_never_exceed_the_configured_bound(make_client, monkeypatch):
    from artifact_relay.routers import auth

    bound = 2
    client = make_client(login_max_concurrent_verifications=bound)

    lock = threading.Lock()
    live = 0
    peak = 0

    def slow_verify(password: str, encoded_hash: str) -> bool:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.05)
            return False
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(auth, "verify_view_password", slow_verify)

    codes: list[int] = []
    codes_lock = threading.Lock()

    def attempt() -> None:
        response = client.post("/login", data={"password": "wrong", "next": "/"})
        with codes_lock:
            codes.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(codes) == 12
    assert peak <= bound, f"{peak} Argon2 verifications ran at once, bound is {bound}"
    assert peak > 0, "the patched verifier was never reached"
    assert set(codes) <= {401, 503}
    assert 503 in codes, "with 12 racing logins and a bound of 2 some must be shed"


def test_the_bound_is_configurable_and_defaults_to_something_small(settings, make_client):
    assert settings.login_max_concurrent_verifications == 4

    client = make_client(login_max_concurrent_verifications=7)

    assert client.app.state.verification_gate.available == 7
