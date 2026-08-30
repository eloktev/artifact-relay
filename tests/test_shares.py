from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from artifact_relay.middleware import redact_path
from artifact_relay.security import ShareSessionSigner
from tests.conftest import API_TOKEN, VIEW_PASSWORD


def test_disabled_sharing_hides_the_share_panel(make_client):  # type: ignore[no-untyped-def]
    client = make_client(share_links_enabled=False)
    published = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "Private report", "format": "markdown"},
        files={"content": ("report.md", b"private body", "text/markdown")},
    )
    artifact_id = published.json()["id"]
    assert client.post("/login", data={"password": VIEW_PASSWORD, "next": "/"}).status_code == 200

    page = client.get(f"/a/{artifact_id}")

    assert page.status_code == 200
    assert "Поделиться" not in page.text
    assert f"/a/{artifact_id}/shares" not in page.text


def test_disabled_sharing_returns_404_for_every_share_surface(make_client):  # type: ignore[no-untyped-def]
    client = make_client(share_links_enabled=False)
    published = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "Private report", "format": "html"},
        files=[
            ("content", ("report.html", b"<h1>private body</h1>", "text/html")),
            ("assets", ("plot.png", b"image-bytes", "image/png")),
        ],
    )
    artifact_id = published.json()["id"]
    store = client.app.state.store
    share, token = store.create_share(artifact_id, expires_at=None)
    embed_token = client.app.state.share_embed_capability.issue(share.id, artifact_id)
    assert client.post("/login", data={"password": VIEW_PASSWORD, "next": "/"}).status_code == 200

    responses = [
        client.get(f"/s/{share.id}"),
        client.post(f"/s/{share.id}/redeem", json={"token": token}),
        client.get(f"/s/{share.id}/assets/plot.png"),
        client.get(f"/s/{share.id}/embed/{embed_token}/"),
        client.get(f"/s/{share.id}/embed/{embed_token}/assets/plot.png"),
        client.get(f"/s/{share.id}/embed/{embed_token}/plot.png"),
        client.post(f"/a/{artifact_id}/shares", data={"expires_days": "7"}),
        client.post(f"/a/{artifact_id}/shares/{share.id}/revoke"),
    ]

    assert [response.status_code for response in responses] == [404] * len(responses)
    assert store.get_share(share.id).revoked_at is None
    assert len(store.list_shares(artifact_id)) == 1


def test_share_capability_is_scoped_and_revocable(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(title="Shared report").json()["id"]
    other_id = publish(title="Private report").json()["id"]
    store = client.app.state.store
    now = datetime(2026, 8, 29, tzinfo=UTC)

    share, token = store.create_share(
        artifact_id,
        expires_at=now + timedelta(days=7),
        created_at=now,
    )

    assert token not in share.token_hash
    assert store.authorize_share(share.id, token, now=now) == share
    assert store.authorize_share(share.id, token + "x", now=now) is None
    assert store.authorize_share(share.id, token, artifact_id=other_id, now=now) is None

    assert store.revoke_share(share.id, revoked_at=now + timedelta(minutes=1)) is True
    assert store.authorize_share(share.id, token, now=now + timedelta(minutes=2)) is None
    assert store.delete(artifact_id) is True
    assert store.get_share(share.id) is None


def test_redeem_exchanges_fragment_secret_for_scoped_cookie(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(title="Confidential title", content=b"secret body marker").json()["id"]
    share, token = client.app.state.store.create_share(artifact_id, expires_at=None)

    landing = client.get(f"/s/{share.id}#{token}")

    assert landing.status_code == 200
    assert token not in landing.text
    assert "Confidential title" not in landing.text
    assert "secret body marker" not in landing.text
    assert landing.headers["cache-control"] == "private, no-store"

    redeemed = client.post(f"/s/{share.id}/redeem", json={"token": token})

    assert redeemed.status_code == 204
    cookie = redeemed.headers["set-cookie"]
    assert token not in cookie
    assert f"Path=/s/{share.id}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


def test_redeemed_link_reads_only_its_artifact(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(title="Shared report", content=b"allowed-marker").json()["id"]
    private_id = publish(title="Private report", content=b"private-marker").json()["id"]
    store = client.app.state.store
    share, token = store.create_share(artifact_id, expires_at=None)
    private_share, _ = store.create_share(private_id, expires_at=None)
    assert client.post(f"/s/{share.id}/redeem", json={"token": token}).status_code == 204

    shared = client.get(f"/s/{share.id}")
    unrelated = client.get(f"/s/{private_share.id}")
    canonical = client.get(f"/a/{private_id}")
    source = client.get(f"/a/{artifact_id}/source")
    root = client.get("/", follow_redirects=False)
    favorite = client.post(f"/a/{artifact_id}/favorite")
    api = client.get(f"/api/artifacts/{artifact_id}")

    assert shared.status_code == 200
    assert "Shared report" in shared.text
    assert "allowed-marker" in shared.text
    assert "private-marker" not in shared.text
    assert "Private report" not in unrelated.text
    assert "private-marker" not in canonical.text
    assert source.status_code == 403
    assert root.status_code == 303
    assert favorite.status_code == 403
    assert api.status_code == 401


def test_shared_assets_require_the_same_live_share(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(
        title="Chart",
        content=b"![chart](plot.png)",
        assets=[("plot.png", b"image-bytes")],
    ).json()["id"]
    store = client.app.state.store
    share, token = store.create_share(artifact_id, expires_at=None)
    assert client.post(f"/s/{share.id}/redeem", json={"token": token}).status_code == 204

    asset = client.get(f"/s/{share.id}/assets/plot.png")

    assert asset.status_code == 200
    assert asset.content == b"image-bytes"
    assert client.get(f"/a/{artifact_id}/assets/plot.png").status_code == 403

    assert store.revoke_share(share.id) is True
    assert client.get(f"/s/{share.id}").status_code == 404
    assert client.get(f"/s/{share.id}/assets/plot.png").status_code == 404


def test_shared_html_stays_sandboxed_and_revocable(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(
        title="Interactive report",
        fmt="html",
        filename="report.html",
        content=b"<!doctype html><h1>html-marker</h1>",
    ).json()["id"]
    store = client.app.state.store
    share, token = store.create_share(artifact_id, expires_at=None)
    assert client.post(f"/s/{share.id}/redeem", json={"token": token}).status_code == 204

    page = client.get(f"/s/{share.id}")

    assert page.status_code == 200
    assert "html-marker" not in page.text
    match = re.search(r'src="([^"]+/embed/[^"]+/)"', page.text)
    assert match is not None
    iframe = client.get(match.group(1))
    assert iframe.status_code == 200
    assert b"html-marker" in iframe.content
    assert "sandbox" in page.text

    assert store.revoke_share(share.id) is True
    assert client.get(match.group(1)).status_code == 404


def test_revoked_html_embed_cannot_be_replayed_through_another_share(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(
        title="Interactive report",
        fmt="html",
        filename="report.html",
        content=b"<!doctype html><h1>html-marker</h1>",
    ).json()["id"]
    store = client.app.state.store
    revoked_share, revoked_token = store.create_share(artifact_id, expires_at=None)
    active_share, active_token = store.create_share(artifact_id, expires_at=None)
    assert (
        client.post(f"/s/{revoked_share.id}/redeem", json={"token": revoked_token}).status_code
        == 204
    )
    assert (
        client.post(f"/s/{active_share.id}/redeem", json={"token": active_token}).status_code == 204
    )

    page = client.get(f"/s/{revoked_share.id}")
    match = re.search(rf"/s/{revoked_share.id}/embed/([^/]+)/", page.text)
    assert match is not None
    embed_token = match.group(1)
    assert store.revoke_share(revoked_share.id) is True

    assert client.get(f"/s/{revoked_share.id}/embed/{embed_token}/").status_code == 404
    assert client.get(f"/s/{active_share.id}/embed/{embed_token}/").status_code == 404


def test_viewer_creates_lists_and_revokes_a_share(logged_in, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(title="Share me", content=b"body").json()["id"]

    created = logged_in.post(f"/a/{artifact_id}/shares", data={"expires_days": "7"})

    assert created.status_code == 200
    match = re.search(
        r"https://artifacts\.example\.test/s/([A-Za-z0-9_-]+)#([A-Za-z0-9_-]{43})",
        created.text,
    )
    assert match is not None
    share_id, token = match.groups()
    assert "7 дней" in created.text

    artifact_page = logged_in.get(f"/a/{artifact_id}")
    assert share_id in artifact_page.text
    assert token not in artifact_page.text
    assert "Отозвать" in artifact_page.text

    revoked = logged_in.post(f"/a/{artifact_id}/shares/{share_id}/revoke", follow_redirects=False)
    assert revoked.status_code == 303
    assert logged_in.app.state.store.get_share(share_id).revoked_at is not None


def test_invalid_or_expired_share_never_sets_a_cookie(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish().json()["id"]
    share, token = client.app.state.store.create_share(
        artifact_id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert client.get(f"/s/{share.id}").status_code == 404
    expired = client.post(f"/s/{share.id}/redeem", json={"token": token})
    malformed = client.post(f"/s/{share.id}/redeem", json={"token": "wrong"})

    assert expired.status_code == 403
    assert "set-cookie" not in expired.headers
    assert malformed.status_code == 422
    assert "set-cookie" not in malformed.headers


def test_tampered_or_server_expired_share_cookie_does_not_authorize(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish(content=b"share-cookie-marker").json()["id"]
    share, token = client.app.state.store.create_share(artifact_id, expires_at=None)
    assert client.post(f"/s/{share.id}/redeem", json={"token": token}).status_code == 204
    cookie_name = f"artifact_share_{share.id}"
    good_cookie = client.cookies[cookie_name]

    client.cookies.set(cookie_name, good_cookie + "x", path=f"/s/{share.id}")
    tampered = client.get(f"/s/{share.id}")
    assert "share-cookie-marker" not in tampered.text

    client.cookies.set(cookie_name, good_cookie, path=f"/s/{share.id}")
    client.app.state.share_session_signer = ShareSessionSigner("s" * 48, -1)
    expired = client.get(f"/s/{share.id}")
    assert "share-cookie-marker" not in expired.text


def test_redeem_rejects_extra_fields_and_oversized_bodies(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish().json()["id"]
    share, token = client.app.state.store.create_share(artifact_id, expires_at=None)

    extra = client.post(f"/s/{share.id}/redeem", json={"token": token, "padding": "x"})
    declared = client.post(
        f"/s/{share.id}/redeem",
        content=b'{"token":"' + token.encode() + b'","padding":"' + b"x" * 2048 + b'"}',
        headers={"content-type": "application/json"},
    )
    chunked = client.post(
        f"/s/{share.id}/redeem",
        content=iter(
            [
                b'{"token":"' + token.encode() + b'","padding":"',
                b"x" * 2048,
                b'"}',
            ]
        ),
        headers={"content-type": "application/json"},
    )

    assert extra.status_code == 422
    assert declared.status_code == 413
    assert chunked.status_code == 413


def test_anonymous_oversized_share_creation_is_rejected_before_form_parsing(
    make_client,
):  # type: ignore[no-untyped-def]
    client = make_client(max_content_bytes=1024, max_asset_bytes=1024)
    body = b"expires_days=7&padding=" + b"x" * 100_000

    declared = client.post(
        "/a/not-an-artifact/shares",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    chunked = client.post(
        "/a/not-an-artifact/shares",
        content=iter([body[:1000], body[1000:]]),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert declared.status_code == 413
    assert chunked.status_code == 403


def test_authenticated_oversized_share_creation_is_limited(logged_in):  # type: ignore[no-untyped-def]
    response = logged_in.post(
        "/a/not-an-artifact/shares",
        content=b"expires_days=7&padding=" + b"x" * 2048,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413


def test_anonymous_viewer_cannot_create_a_share(client, publish):  # type: ignore[no-untyped-def]
    artifact_id = publish().json()["id"]

    response = client.post(f"/a/{artifact_id}/shares", data={"expires_days": "7"})

    assert response.status_code == 403
    assert client.app.state.store.list_shares(artifact_id) == []


def test_share_embed_capability_is_redacted_from_access_logs():
    token = "secret-signed-iframe-token"
    path = f"/s/public-id/embed/{token}/assets/chart.png"

    assert redact_path(path) == "/s/public-id/embed/***/assets/chart.png"
