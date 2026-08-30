"""Regression: the deployed process must not blanket-trust `X-Forwarded-For`.

The login throttle keys on `request.client.host`. If uvicorn is told to trust proxy headers
from *any* peer, a client that sets its own `X-Forwarded-For` gets a fresh rate-limit bucket
per request and the throttle stops existing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def dockerfile_cmd() -> list[str]:
    """The image's CMD as a token list, joining backslash continuations."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", "")
    match = re.search(r"^CMD\s+(\[.*?\])\s*$", text, re.MULTILINE | re.DOTALL)
    assert match, "the Dockerfile has no JSON-form CMD"
    tokens: list[str] = json.loads(match.group(1))
    return tokens


def test_dockerfile_does_not_hardcode_a_wildcard_forwarded_allow_ips():
    tokens = dockerfile_cmd()

    assert "--forwarded-allow-ips" not in tokens, (
        "passing --forwarded-allow-ips on the command line shadows FORWARDED_ALLOW_IPS, "
        "so the documented environment variable cannot narrow the trusted peers"
    )
    assert "*" not in tokens
    assert "--proxy-headers" in tokens, "the real client address still comes from the proxy"


def test_uvicorn_trusts_only_localhost_when_the_variable_is_unset(monkeypatch):
    from uvicorn.config import Config

    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    config = Config("artifact_relay.main:app", proxy_headers=True)

    assert config.forwarded_allow_ips == "127.0.0.1"


@pytest.mark.parametrize("value", ["10.0.0.0/8", "172.18.0.1", "192.168.1.5,10.0.0.7"])
def test_forwarded_allow_ips_environment_variable_reaches_uvicorn(monkeypatch, value):
    from uvicorn.config import Config

    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    config = Config("artifact_relay.main:app", proxy_headers=True)

    assert config.forwarded_allow_ips == value


def test_the_throttle_key_is_the_peer_address_not_a_spoofed_header(client):
    """A forged `X-Forwarded-For` must not produce a fresh rate-limit bucket."""
    from starlette.requests import Request

    from artifact_relay.dependencies import client_key

    def request_with(header: str | None) -> Request:
        headers = [(b"x-forwarded-for", header.encode())] if header else []
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "path": "/login",
                "headers": headers,
                "client": ("10.9.8.7", 4242),
                "scheme": "https",
                "server": ("testserver", 443),
            }
        )

    assert client_key(request_with(None)) == "10.9.8.7"
    assert client_key(request_with("203.0.113.9")) == "10.9.8.7"
    assert client_key(request_with("203.0.113.10")) == "10.9.8.7"


def test_documentation_no_longer_advertises_a_wildcard_as_shipped():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "FORWARDED_ALLOW_IPS" in env_example, "the setting must appear in the template"
    assert "`--forwarded-allow-ips` is `*` in the image" not in readme
    assert "FORWARDED_ALLOW_IPS" in readme
