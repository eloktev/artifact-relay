from tests.conftest import BASE_URL, VIEW_PASSWORD

SECRET_BODY = "СОВЕРШЕННО-СЕКРЕТНОЕ-ТЕЛО-ДОКУМЕНТА"


def test_anonymous_request_gets_the_login_shell_with_og_metadata_but_no_body(client, publish):
    created = publish(
        title="Секретный отчёт",
        summary="Краткое содержание для Telegram",
        content=f"# Заголовок\n\n{SECRET_BODY}\n".encode(),
    ).json()
    artifact_id = created["id"]

    page = client.get(f"/a/{artifact_id}")

    assert page.status_code == 200
    assert SECRET_BODY not in page.text, "private body leaked to an anonymous visitor"

    html = page.text
    assert 'property="og:title" content="Секретный отчёт"' in html
    assert 'property="og:description" content="Краткое содержание для Telegram"' in html
    assert f'property="og:url" content="{BASE_URL}/a/{artifact_id}"' in html
    assert 'property="og:type" content="article"' in html
    assert f'property="og:image" content="{BASE_URL}/a/{artifact_id}/og.png"' in html
    assert 'name="robots" content="noindex, nofollow, noarchive"' in html
    assert page.headers["x-robots-tag"] == "noindex, nofollow, noarchive"

    # The shell must let the visitor authenticate and land back on the artifact.
    assert 'type="password"' in html
    assert f'value="/a/{artifact_id}"' in html


def test_login_from_the_shell_returns_to_the_artifact(client, publish):
    artifact_id = publish(content=f"# H\n\n{SECRET_BODY}\n".encode()).json()["id"]

    response = client.post(
        "/login",
        data={"password": VIEW_PASSWORD, "next": f"/a/{artifact_id}"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/a/{artifact_id}"

    page = client.get(f"/a/{artifact_id}")
    assert page.status_code == 200
    assert SECRET_BODY in page.text


def test_unknown_artifact_is_404_for_authenticated_and_anonymous_alike(client):
    assert client.get("/a/definitely-not-a-real-artifact-id").status_code == 404
