import pytest

SECRET = "ТЕЛО-ИСТЁКШЕГО-ДОКУМЕНТА"


@pytest.mark.parametrize("suffix", ["", "/source", "/og.png"])
def test_expired_artifact_is_gone_everywhere(publish, logged_in, expire_artifact, suffix):
    artifact_id = publish(content=f"# t\n\n{SECRET}\n".encode()).json()["id"]
    expire_artifact(artifact_id)

    response = logged_in.get(f"/a/{artifact_id}{suffix}")

    assert response.status_code == 410, response.status_code
    assert SECRET not in response.text


def test_expired_html_artifact_embed_and_assets_are_gone(publish, logged_in, expire_artifact):
    artifact_id = publish(
        fmt="html", content=f"<p>{SECRET}</p>".encode(), assets=[("a.png", b"\x89PNG")]
    ).json()["id"]
    # A capability minted while the artifact was live must stop working the moment it expires:
    # the routes behind it still load the artifact through the normal 404/410 path.
    embed = logged_in.app.state.embed_capability.path_for(artifact_id)
    expire_artifact(artifact_id)

    assert logged_in.get(embed).status_code == 410
    assert logged_in.get(f"{embed}assets/a.png").status_code == 410
    assert logged_in.get(f"/a/{artifact_id}/assets/a.png").status_code == 410


def test_expiry_is_hidden_from_anonymous_visitors_too(publish, client, expire_artifact):
    artifact_id = publish(content=f"# t\n\n{SECRET}\n".encode()).json()["id"]
    expire_artifact(artifact_id)

    response = client.get(f"/a/{artifact_id}")

    assert response.status_code == 410
    assert SECRET not in response.text


def test_pinned_artifacts_never_expire(publish, logged_in):
    artifact_id = publish(content=f"# t\n\n{SECRET}\n".encode(), expires_in_days=0).json()["id"]

    page = logged_in.get(f"/a/{artifact_id}")

    assert page.status_code == 200
    assert SECRET in page.text


def test_the_410_page_is_a_readable_html_page(publish, logged_in, expire_artifact):
    artifact_id = publish().json()["id"]
    expire_artifact(artifact_id)

    response = logged_in.get(f"/a/{artifact_id}")

    assert "text/html" in response.headers["content-type"]
    assert "истёк" in response.text.lower() or "истек" in response.text.lower()
