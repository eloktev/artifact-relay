"""Regression: a sandboxed HTML artifact must be able to load its own assets.

`<iframe sandbox="allow-scripts">` (no `allow-same-origin`) puts the artifact document in an
**opaque origin**. Three separate things then break for every subresource it requests:

1. an opaque origin's requests are `site-for-cookies: null`, so the `SameSite=Lax` session
   cookie is not sent and the session-gated asset route answers 403;
2. `Cross-Origin-Resource-Policy: same-origin` blocks the response anyway, because an opaque
   origin is same-origin with nothing;
3. `'self'` in a CSP matches nothing there, so every allowance must name an absolute prefix.

The fix is a signed, expiring, artifact-bound capability path. The token sits in a *directory*
segment above the document so that `assets/chart.png` still resolves relatively, and the
routes under it authenticate from the token alone — never from a cookie, never from the API
token, and never for a different artifact.
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import API_TOKEN, BASE_URL, VIEW_PASSWORD

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

ARTIFACT = """<!doctype html><html lang="ru"><head><meta charset="utf-8"></head>
<body><h1>ИНФОГРАФИКА-МАРКЕР</h1>
<img src="assets/chart.png" alt="a"><img src="chart.png" alt="b">
<script>document.title = "ok";</script></body></html>
"""

# Headers a browser actually sends for a subresource fetched by an opaque-origin document:
# no cookie is attached at all, the origin is the literal string "null", and the request is
# classified as cross-site.
OPAQUE_ORIGIN_HEADERS = {
    "Origin": "null",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Dest": "image",
}


@pytest.fixture
def anonymous(client):
    """A second client over the *same* app that has never logged in.

    `logged_in` returns the shared `client` object itself, so asking that one for a
    capability URL would prove nothing: it carries a session cookie. The opaque origin
    inside the iframe carries none, and that is exactly the case under test. The app is
    already running, so this client is used without re-entering the lifespan.
    """
    from fastapi.testclient import TestClient

    return TestClient(client.app, base_url=BASE_URL)


def iframe_src(page: str) -> str:
    match = re.search(r'<iframe[^>]*\ssrc="([^"]*)"', page)
    assert match, f"no iframe in the page: {page[:400]}"
    return match.group(1)


@pytest.fixture
def embedded(publish, logged_in):
    """Publish an HTML artifact with an asset and return (id, iframe src, page)."""
    artifact_id = publish(
        fmt="html", content=ARTIFACT.encode(), assets=[("chart.png", PNG)]
    ).json()["id"]
    page = logged_in.get(f"/a/{artifact_id}").text
    return artifact_id, iframe_src(page), page


def test_the_iframe_points_at_a_capability_path_that_keeps_relative_assets_working(embedded):
    artifact_id, src, _ = embedded

    assert src.startswith(f"/embed/{artifact_id}/"), src
    assert src.endswith("/"), (
        "the document URL must end in a slash so that a relative `assets/chart.png` "
        f"resolves inside the capability path, not above it: {src}"
    )
    token = src[len(f"/embed/{artifact_id}/") :].rstrip("/")
    assert token and "/" not in token
    assert len(token) > 20, "a capability token must not be guessable"


def test_the_document_loads_with_no_cookie_at_all(anonymous, embedded):
    """`client` has never logged in — the capability is the only credential."""
    _, src, _ = embedded

    response = anonymous.get(src, headers={"Sec-Fetch-Dest": "iframe"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "ИНФОГРАФИКА-МАРКЕР" in response.text
    assert "<script>" in response.text, "inline JS must survive: it is the point of html"
    assert not response.cookies, "the capability route must not issue cookies"


@pytest.mark.parametrize("reference", ["assets/chart.png", "chart.png"])
def test_an_opaque_origin_subresource_request_is_served(anonymous, embedded, reference):
    """The real failure mode: no cookie, Origin: null, Sec-Fetch-Site: cross-site."""
    _, src, _ = embedded

    response = anonymous.get(f"{src}{reference}", headers=OPAQUE_ORIGIN_HEADERS)

    assert response.status_code == 200, f"{reference} -> {response.status_code}"
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    # `same-origin` would be blocked by the browser: an opaque origin matches no origin.
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_csp_asset_prefix_covers_the_capability_path(anonymous, embedded, settings):
    _artifact_id, src, _ = embedded

    policy = anonymous.get(src).headers["content-security-policy"]
    prefix = settings.absolute_url(src)

    assert f"img-src {prefix} data:" in policy
    assert f"font-src {prefix} data:" in policy
    assert f"media-src {prefix}" in policy
    assert "'self'" not in policy.split("frame-ancestors")[0], (
        "'self' matches nothing in an opaque origin"
    )
    assert "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy
    assert "connect-src 'none'" in policy
    assert "form-action 'none'" in policy
    assert "frame-ancestors 'self'" in policy


def test_the_sandbox_attribute_is_unchanged(embedded):
    _, _, page = embedded

    sandbox = re.search(r'<iframe[^>]*\ssandbox="([^"]*)"', page)
    assert sandbox
    assert set(sandbox.group(1).split()) == {"allow-scripts"}


# --- the capability must be narrow -------------------------------------------


def test_a_token_minted_for_one_artifact_cannot_read_another(publish, logged_in, anonymous):
    first = publish(fmt="html", content=ARTIFACT.encode(), assets=[("chart.png", PNG)]).json()["id"]
    second = publish(
        fmt="html", content=b"<html><body>SECOND-SECRET</body></html>", assets=[("chart.png", PNG)]
    ).json()["id"]

    src = iframe_src(logged_in.get(f"/a/{first}").text)
    token = src[len(f"/embed/{first}/") :].rstrip("/")

    crossed = anonymous.get(f"/embed/{second}/{token}/")

    assert crossed.status_code == 403, crossed.text
    assert "SECOND-SECRET" not in crossed.text
    assert anonymous.get(f"/embed/{second}/{token}/assets/chart.png").status_code == 403


@pytest.mark.parametrize(
    "mangle",
    [
        # An interior character, deliberately: the last character of an *unpadded* base64url
        # string carries only a few significant bits, so flipping it can decode to the same
        # bytes and leave the signature valid.
        lambda t: t[:5] + ("a" if t[5] != "a" else "b") + t[6:],
        lambda t: t[:-8] + ("a" if t[-8] != "a" else "b") + t[-7:],
        lambda t: t.upper(),
        lambda t: "",
        lambda t: "not-a-token",
        lambda t: t.split(".")[0],
    ],
)
def test_a_tampered_or_absent_token_is_refused(anonymous, embedded, mangle):
    artifact_id, src, _ = embedded
    token = src[len(f"/embed/{artifact_id}/") :].rstrip("/")

    response = anonymous.get(f"/embed/{artifact_id}/{mangle(token) or 'x'}/")

    assert response.status_code in (403, 404), response.status_code
    assert "ИНФОГРАФИКА-МАРКЕР" not in response.text


def test_an_expired_capability_is_refused(anonymous, embedded):
    _artifact_id, src, _ = embedded

    assert anonymous.get(src).status_code == 200
    # Any signature is older than a negative maximum age, so this deterministically
    # exercises the expiry branch without sleeping.
    anonymous.app.state.embed_capability.ttl_seconds = -1

    assert anonymous.get(src).status_code == 403
    assert anonymous.get(f"{src}assets/chart.png").status_code == 403


def test_the_capability_does_not_leak_the_session_cookie_or_the_api_token(embedded, logged_in):
    _artifact_id, src, page = embedded

    assert API_TOKEN not in page and API_TOKEN not in src
    assert VIEW_PASSWORD not in page
    session = logged_in.cookies.get("ap_session")
    assert session
    assert session not in page and session not in src


def test_the_capability_grants_the_document_and_its_assets_and_nothing_else(anonymous, embedded):
    artifact_id, src, _ = embedded

    assert anonymous.get(f"{src}assets/chart.png").status_code == 200
    # Not a route into the rest of the service.
    assert anonymous.get(f"/a/{artifact_id}/source").status_code == 403
    assert anonymous.get(f"/a/{artifact_id}/assets/chart.png").status_code == 403
    assert anonymous.delete(f"/api/artifacts/{artifact_id}").status_code == 401
    assert anonymous.get(f"{src}../../a/{artifact_id}/source").status_code in (403, 404)


# A literal `..` is collapsed by the HTTP client before the request is sent, so traversal has
# to be expressed percent-encoded to reach the route at all. Starlette decodes the path
# parameter before the handler sees it, which is precisely where the name allowlist bites.
HOSTILE_NAMES = [
    "missing.png",
    "source",
    "%2e%2e",
    "%2e%2e%2fsource",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%2f..%2fartifacts.db",
    ".hidden",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_an_unregistered_asset_name_is_not_served_through_the_capability(anonymous, embedded, name):
    _, src, _ = embedded

    for url in (f"{src}assets/{name}", f"{src}{name}"):
        response = anonymous.get(url)
        assert response.status_code in (403, 404), f"{url} -> {response.status_code}"
        assert b"root:" not in response.content
        assert b"SQLite format" not in response.content


def test_a_markdown_artifact_has_no_capability_path(publish, logged_in):
    artifact_id = publish(content=b"# hi\n").json()["id"]
    page = logged_in.get(f"/a/{artifact_id}").text

    assert "<iframe" not in page
    assert "/embed/" not in page


def test_an_expired_artifact_revokes_a_live_capability(anonymous, embedded, expire_artifact):
    artifact_id, src, _ = embedded

    expire_artifact(artifact_id)

    assert anonymous.get(src).status_code == 410
    assert anonymous.get(f"{src}assets/chart.png").status_code == 410


def test_a_deleted_artifact_revokes_a_live_capability(anonymous, embedded):
    artifact_id, src, _ = embedded

    assert (
        anonymous.delete(
            f"/api/artifacts/{artifact_id}", headers={"Authorization": f"Bearer {API_TOKEN}"}
        ).status_code
        == 204
    )

    assert anonymous.get(src).status_code == 404
    assert anonymous.get(f"{src}assets/chart.png").status_code == 404


def test_the_capability_token_is_kept_out_of_the_access_log(caplog, anonymous, embedded):
    import logging

    artifact_id, src, _ = embedded
    token = src[len(f"/embed/{artifact_id}/") :].rstrip("/")

    with caplog.at_level(logging.INFO, logger="artifact_relay.access"):
        anonymous.get(f"{src}assets/chart.png")

    logged = [record.__dict__.get("path", "") for record in caplog.records]
    assert logged, "the request was not logged at all"
    assert not any(token in path for path in logged), logged
    assert any(path.startswith(f"/embed/{artifact_id}/***") for path in logged), logged


def test_no_referrer_is_sent_so_the_token_cannot_leak_sideways(anonymous, embedded):
    _, src, _ = embedded

    assert anonymous.get(src).headers["referrer-policy"] == "no-referrer"
