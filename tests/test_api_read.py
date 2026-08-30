def test_agent_can_read_artifact_source_with_api_token(publish, client, settings):
    created = publish(
        title="Private report",
        fmt="markdown",
        content=b"# Result\n\nAgent-readable body.\n",
        assets=[("chart.png", b"PNG")],
        filename="report.md",
        summary="Short preview",
    ).json()

    response = client.get(
        f"/api/artifacts/{created['id']}",
        headers={"Authorization": f"Bearer {settings.artifact_api_token.get_secret_value()}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        **created,
        "source_filename": "report.md",
        "content": "# Result\n\nAgent-readable body.\n",
    }


def test_agent_read_rejects_missing_api_token_without_leaking_body(publish, client):
    created = publish(content=b"PRIVATE BODY").json()

    response = client.get(f"/api/artifacts/{created['id']}")

    assert response.status_code == 401
    assert "PRIVATE BODY" not in response.text


def test_agent_read_honours_artifact_expiry(publish, client, settings, expire_artifact):
    created = publish(content=b"EXPIRED PRIVATE BODY").json()
    expire_artifact(created["id"])

    response = client.get(
        f"/api/artifacts/{created['id']}",
        headers={"Authorization": f"Bearer {settings.artifact_api_token.get_secret_value()}"},
    )

    assert response.status_code == 410
    assert "EXPIRED PRIVATE BODY" not in response.text
