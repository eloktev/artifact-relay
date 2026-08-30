from tests.conftest import API_TOKEN


def auth():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def test_delete_removes_metadata_and_every_byte(client, publish, logged_in):
    created = publish(assets=[("chart.png", b"\x89PNG-data")]).json()
    artifact_id = created["id"]
    store = client.app.state.store
    artifact_dir = store.artifact_dir(artifact_id)

    assert artifact_dir.is_dir()
    assert logged_in.get(f"/a/{artifact_id}").status_code == 200

    response = client.delete(f"/api/artifacts/{artifact_id}", headers=auth())

    assert response.status_code == 204
    assert not response.content
    assert not artifact_dir.exists(), "artifact bytes survived deletion"
    assert store.get(artifact_id) is None
    assert store.list_assets(artifact_id) == []

    assert logged_in.get(f"/a/{artifact_id}").status_code == 404
    assert logged_in.get(f"/a/{artifact_id}/source").status_code == 404
    assert logged_in.get(f"/a/{artifact_id}/assets/chart.png").status_code == 404
    assert logged_in.get(f"/a/{artifact_id}/og.png").status_code == 404


def test_delete_requires_the_bearer_token(client, publish):
    artifact_id = publish().json()["id"]

    assert client.delete(f"/api/artifacts/{artifact_id}").status_code == 401
    assert (
        client.delete(
            f"/api/artifacts/{artifact_id}", headers={"Authorization": "Bearer wrong-token-value"}
        ).status_code
        == 401
    )
    assert client.app.state.store.get(artifact_id) is not None


def test_a_viewer_session_does_not_authorise_deletion(logged_in, publish):
    artifact_id = publish().json()["id"]

    assert logged_in.delete(f"/api/artifacts/{artifact_id}").status_code == 401
    assert logged_in.app.state.store.get(artifact_id) is not None


def test_deleting_an_unknown_artifact_is_404(client):
    assert client.delete("/api/artifacts/not-a-real-id", headers=auth()).status_code == 404


def test_delete_is_idempotent_after_the_first_call(client, publish):
    artifact_id = publish().json()["id"]

    assert client.delete(f"/api/artifacts/{artifact_id}", headers=auth()).status_code == 204
    assert client.delete(f"/api/artifacts/{artifact_id}", headers=auth()).status_code == 404


def test_delete_rejects_hostile_identifiers(client):
    for hostile in ("..", "%2e%2e", "a/../../b", "*"):
        response = client.delete(f"/api/artifacts/{hostile}", headers=auth())
        assert response.status_code in (404, 422), (hostile, response.status_code)


def test_store_never_walks_outside_its_own_directory(tmp_path):
    """Regression: `delete("..")` used to rmtree the whole data directory."""
    from artifact_relay.storage import ArtifactStore

    store = ArtifactStore(tmp_path / "data")
    store.initialize()
    canary = store.data_dir / "canary.txt"
    canary.write_text("do not delete me")

    for hostile in ("..", ".", "../..", "a/b", "", "/", "\\", "x" * 500):
        assert store.get(hostile) is None
        assert store.delete(hostile) is False
        assert store.get_asset(hostile, "a.png") is None

    assert canary.is_file(), "the store deleted files outside an artifact directory"
    assert store.db_path.is_file()
