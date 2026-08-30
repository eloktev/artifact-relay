"""Regression: a sub-resource error must not be answered with an HTML page.

`wants_html` treated "everything outside /api/" as a browser route, so a missing image gave
`<img>` a full HTML error document, a failed asset fetch inside the sandboxed iframe got an
HTML body under a CSP that forbids rendering it, and a `curl` of a source download got markup
where the caller asked for bytes. Pages that a *person* navigates to keep their rendered
page; things a *program* fetches get JSON.
"""

from __future__ import annotations

import pytest

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def artifact_id(publish):
    return publish(assets=[("chart.png", PNG)]).json()["id"]


@pytest.mark.parametrize(
    "path",
    [
        "/a/{id}/assets/missing.png",
        "/a/{id}/assets/chart.png",
        "/a/{id}/source",
    ],
)
def test_sub_resource_errors_are_json(client, artifact_id, path):
    response = client.get(path.format(id=artifact_id))

    assert response.status_code in (403, 404), response.status_code
    assert response.headers["content-type"].startswith("application/json"), response.text
    assert "<html" not in response.text.lower()
    assert "detail" in response.json()


def test_embed_errors_are_json(client, publish):
    html_id = publish(fmt="html", content=b"<p>x</p>").json()["id"]

    response = client.get(f"/embed/{html_id}/forged-token/")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert "<html" not in response.text.lower()


def test_a_missing_og_card_is_json_not_a_page(client):
    """Telegram fetches this; it wants an image or a machine-readable failure."""
    response = client.get("/a/AAAAAAAAAAAAAAAAAAAAAAAA/og.png")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "path",
    ["/a/AAAAAAAAAAAAAAAAAAAAAAAA", "/login", "/nope", "/a/short"],
)
def test_pages_a_person_navigates_to_still_get_a_rendered_page(client, path):
    response = client.get(path, follow_redirects=False)

    assert response.headers["content-type"].startswith("text/html"), path


def test_the_artifact_page_itself_is_still_html_when_it_is_gone(client, publish, expire_artifact):
    gone = publish().json()["id"]
    expire_artifact(gone)

    response = client.get(f"/a/{gone}")

    assert response.status_code == 410
    assert response.headers["content-type"].startswith("text/html")
    assert "Срок хранения истёк" in response.text


def test_api_errors_are_unchanged(client):
    response = client.post("/api/artifacts")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")


def test_the_json_error_still_carries_the_response_headers(client, make_client):
    """A 429 must keep its Retry-After even when it is rendered as JSON elsewhere."""
    limited = make_client(login_max_attempts=1)
    limited.post("/login", data={"password": "wrong", "next": "/"})
    throttled = limited.post("/login", data={"password": "wrong", "next": "/"})

    assert throttled.status_code == 429
    assert throttled.headers["retry-after"]
