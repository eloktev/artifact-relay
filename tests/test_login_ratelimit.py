from tests.conftest import VIEW_PASSWORD


def test_repeated_failures_are_throttled_even_for_the_right_password(make_client):
    client = make_client(login_max_attempts=3, login_window_seconds=3600)

    for _ in range(3):
        assert (
            client.post("/login", data={"password": "нет"}, follow_redirects=False).status_code
            == 401
        )

    blocked = client.post("/login", data={"password": "нет"}, follow_redirects=False)
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers

    # Throttling must hold even when the caller finally guesses correctly.
    assert (
        client.post("/login", data={"password": VIEW_PASSWORD}, follow_redirects=False).status_code
        == 429
    )


def test_successful_login_clears_the_counter(make_client):
    client = make_client(login_max_attempts=3, login_window_seconds=3600)

    client.post("/login", data={"password": "нет"}, follow_redirects=False)
    client.post("/login", data={"password": "нет"}, follow_redirects=False)
    assert (
        client.post("/login", data={"password": VIEW_PASSWORD}, follow_redirects=False).status_code
        == 303
    )

    for _ in range(3):
        assert (
            client.post("/login", data={"password": "нет"}, follow_redirects=False).status_code
            == 401
        )


def test_window_expiry_lets_a_client_try_again():
    from artifact_relay.ratelimit import FixedWindowRateLimiter

    limiter = FixedWindowRateLimiter(max_attempts=2, window_seconds=60)

    limiter.register_failure("1.2.3.4", now=100.0)
    limiter.register_failure("1.2.3.4", now=100.0)
    assert limiter.retry_after("1.2.3.4", now=100.0) > 0
    assert limiter.retry_after("1.2.3.4", now=161.0) == 0


def test_limiter_memory_is_bounded():
    from artifact_relay.ratelimit import FixedWindowRateLimiter

    limiter = FixedWindowRateLimiter(max_attempts=2, window_seconds=60, max_keys=16)

    for index in range(5000):
        limiter.register_failure(f"10.0.{index // 256}.{index % 256}", now=float(index))

    assert len(limiter) <= 16
