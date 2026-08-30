import re

HTML_ARTIFACT = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<style>body { font-family: system-ui; }</style></head>
<body>
<h1>ИНФОГРАФИКА-МАРКЕР</h1>
<script>document.getElementById("x") && (document.title = "ok");</script>
<img src="assets/chart.png" alt="chart">
</body></html>
"""

NEVER_GRANTED = {
    "allow-same-origin",
    "allow-top-navigation",
    "allow-top-navigation-by-user-activation",
    "allow-top-navigation-to-custom-protocols",
    "allow-popups",
    "allow-popups-to-escape-sandbox",
    "allow-forms",
    "allow-modals",
    "allow-downloads",
    "allow-pointer-lock",
    "allow-presentation",
    "allow-orientation-lock",
    "allow-storage-access-by-user-activation",
}


def iframe_src(page: str) -> str:
    match = re.search(r'<iframe[^>]*\ssrc="([^"]*)"', page)
    assert match, f"no iframe in the page: {page[:400]}"
    return match.group(1)


def parse_csp(header: str) -> dict[str, list[str]]:
    policy: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            policy[parts[0]] = parts[1:]
    return policy


def test_html_artifact_is_framed_and_never_inlined(publish, logged_in):
    artifact_id = publish(fmt="html", content=HTML_ARTIFACT.encode()).json()["id"]

    page = logged_in.get(f"/a/{artifact_id}")

    assert page.status_code == 200
    assert "ИНФОГРАФИКА-МАРКЕР" not in page.text, "artifact HTML was inlined into the viewer page"

    match = re.search(r"<iframe[^>]*>", page.text)
    assert match, "html artifacts must be rendered inside an iframe"
    iframe = match.group(0)

    # The document is addressed by a capability path, not a session-gated one: the opaque
    # origin the sandbox creates cannot send the session cookie. See test_embed_capability.
    assert re.search(rf'src="/embed/{artifact_id}/[^"]+/"', iframe), iframe
    sandbox = re.search(r'sandbox="([^"]*)"', iframe)
    assert sandbox, f"iframe has no sandbox attribute: {iframe}"
    tokens = set(sandbox.group(1).split())

    assert tokens == {"allow-scripts"}, tokens
    assert not tokens & NEVER_GRANTED


def test_embedded_document_is_locked_down_with_csp(publish, logged_in, settings):
    artifact_id = publish(fmt="html", content=HTML_ARTIFACT.encode()).json()["id"]
    src = iframe_src(logged_in.get(f"/a/{artifact_id}").text)

    raw = logged_in.get(src)

    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("text/html")
    assert "ИНФОГРАФИКА-МАРКЕР" in raw.text
    assert "<script>" in raw.text, "inline JS must survive: it is the point of html artifacts"

    policy = parse_csp(raw.headers["content-security-policy"])

    assert policy["default-src"] == ["'none'"]
    assert policy["connect-src"] == ["'none'"]
    assert policy["frame-src"] == ["'none'"]
    assert policy["child-src"] == ["'none'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["form-action"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["worker-src"] == ["'none'"]
    assert policy["manifest-src"] == ["'none'"]
    assert policy["frame-ancestors"] == ["'self'"]

    # Inline scripts/styles only: no remote origin may appear anywhere in the policy.
    assert set(policy["script-src"]) == {"'unsafe-inline'"}
    assert set(policy["style-src"]) == {"'unsafe-inline'"}

    asset_prefix = settings.absolute_url(src)
    assert set(policy["img-src"]) == {asset_prefix, "data:"}
    assert set(policy["font-src"]) == {asset_prefix, "data:"}
    assert set(policy["media-src"]) == {asset_prefix}

    # Defence in depth: the response repeats the sandbox as a CSP directive, so the
    # restriction survives even if the artifact is ever fetched outside our iframe.
    assert set(policy["sandbox"]) == {"allow-scripts"}

    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["x-frame-options"] == "SAMEORIGIN"


def test_the_document_is_unreachable_without_a_capability(client, publish):
    """The old session-gated `/a/<id>/raw` is gone, and guessing the path does not work."""
    artifact_id = publish(fmt="html", content=HTML_ARTIFACT.encode()).json()["id"]

    for path in (
        f"/a/{artifact_id}/raw",
        f"/embed/{artifact_id}/",
        f"/embed/{artifact_id}/guessed-token/",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (401, 403, 404, 307), path
        assert "ИНФОГРАФИКА-МАРКЕР" not in response.text


def test_a_capability_cannot_be_pointed_at_a_markdown_artifact(publish, logged_in):
    """Markdown is rendered and sanitised on this origin; it must never be served raw."""
    artifact_id = publish(fmt="markdown", content=b"# hi\n").json()["id"]
    embed = logged_in.app.state.embed_capability.path_for(artifact_id)

    assert logged_in.get(embed).status_code == 404


def test_markdown_artifacts_are_not_framed(publish, logged_in):
    artifact_id = publish(fmt="markdown", content=b"# hi\n").json()["id"]

    assert "<iframe" not in logged_in.get(f"/a/{artifact_id}").text
