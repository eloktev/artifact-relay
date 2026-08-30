def test_openapi_schema_documents_the_publisher_api(client, settings):
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    schema = response.json()

    assert schema["info"]["title"] == "Artifact Relay"
    paths = schema["paths"]
    assert "/api/artifacts" in paths
    assert "post" in paths["/api/artifacts"]
    assert "/api/artifacts/{artifact_id}" in paths
    assert "delete" in paths["/api/artifacts/{artifact_id}"]
    assert "/api/health" in paths

    # The viewer is server-rendered HTML, not part of the machine-facing contract.
    assert not [p for p in paths if p.startswith("/a/")]
    assert "/login" not in paths

    body = response.text
    for secret in (
        settings.artifact_api_token.get_secret_value(),
        settings.session_secret_key.get_secret_value(),
        settings.view_password_hash.get_secret_value(),
    ):
        assert secret not in body


def test_publish_operation_declares_its_multipart_fields(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/artifacts"]["post"]
    content = operation["requestBody"]["content"]
    assert "multipart/form-data" in content

    ref = content["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][ref]["properties"]

    assert set(properties) == {
        "title",
        "format",
        "content",
        "summary",
        "expires_in_days",
        "assets",
        "session_id",
        "session_title",
        "platform",
        "chat_name",
        "topic_id",
        "topic_name",
    }
    assert set(schema["components"]["schemas"][ref]["required"]) == {"title", "format", "content"}


def test_docs_page_is_served(client):
    response = client.get("/api/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_root_sends_a_visitor_to_the_login_page(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_health_is_the_only_unauthenticated_api_route(client):
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/artifacts").status_code == 401
    assert client.delete("/api/artifacts/whatever").status_code == 401
