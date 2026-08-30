from datetime import UTC, datetime, timedelta


def test_sweep_removes_expired_artifacts_and_their_bytes(client, publish, expire_artifact):
    from artifact_relay.janitor import sweep

    keep = publish(title="Живой").json()["id"]
    pinned = publish(title="Закреплённый", expires_in_days=0).json()["id"]
    doomed = publish(title="Истёкший", assets=[("a.png", b"data")]).json()["id"]
    expire_artifact(doomed)

    store = client.app.state.store
    doomed_dir = store.artifact_dir(doomed)
    assert doomed_dir.is_dir()

    result = sweep(store)

    assert result.expired == 1
    assert store.get(doomed) is None
    assert not doomed_dir.exists()
    assert store.get(keep) is not None
    assert store.get(pinned) is not None
    assert store.artifact_dir(keep).is_dir()


def test_sweep_removes_orphan_directories_left_by_a_crash(client, publish):
    from artifact_relay.janitor import sweep

    store = client.app.state.store
    live = publish().json()["id"]

    orphan = store.artifacts_dir / ("z" * 32)
    orphan.mkdir()
    (orphan / "source").write_bytes(b"leftover")
    # A directory that has only just appeared may be a publish in flight, so the sweep
    # deliberately leaves it alone until the grace period has passed.
    import os

    from artifact_relay.janitor import ORPHAN_GRACE_SECONDS

    assert sweep(store).orphans == 0
    old = datetime.now(UTC).timestamp() - ORPHAN_GRACE_SECONDS - 60
    os.utime(orphan, (old, old))

    result = sweep(store)

    assert result.orphans == 1
    assert not orphan.exists()
    assert store.artifact_dir(live).is_dir()


def test_sweep_removes_stale_staging_directories(client):
    from artifact_relay.janitor import STAGING_MAX_AGE_SECONDS, sweep

    store = client.app.state.store
    stale = store.tmp_dir / "stage-deadbeef"
    stale.mkdir()
    import os

    old = datetime.now(UTC).timestamp() - STAGING_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    fresh = store.tmp_dir / "stage-cafebabe"
    fresh.mkdir()

    result = sweep(store)

    assert result.staging == 1
    assert not stale.exists()
    assert fresh.exists()


def test_sweep_never_touches_the_database_file(client, publish):
    from artifact_relay.janitor import sweep

    store = client.app.state.store
    publish()

    sweep(store)

    assert store.db_path.is_file()
    assert store.tmp_dir.is_dir()
    assert store.artifacts_dir.is_dir()


def test_startup_sweeps_artifacts_that_expired_while_the_process_was_down(tmp_path):
    from fastapi.testclient import TestClient

    from artifact_relay.app import create_app
    from artifact_relay.config import Settings
    from artifact_relay.storage import ArtifactStore

    data_dir = tmp_path / "data"
    store = ArtifactStore(data_dir)
    store.initialize()
    stale = store.create(
        title="Протухший",
        summary=None,
        fmt="markdown",
        content=b"# gone\n",
        source_filename="s.md",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    live = store.create(
        title="Живой",
        summary=None,
        fmt="markdown",
        content=b"# here\n",
        source_filename="s.md",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert store.artifact_dir(stale.id).is_dir()

    settings = Settings(
        data_dir=data_dir,
        base_url="https://artifacts.example.test",
        artifact_api_token="test-token-abcdefghijklmnop",
        view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        session_secret_key="s" * 48,
    )
    with TestClient(create_app(settings)):
        pass

    assert store.get(stale.id) is None
    assert not store.artifact_dir(stale.id).exists()
    assert store.get(live.id) is not None


def test_janitor_loop_sweeps_repeatedly_until_stopped(tmp_path):
    import asyncio

    from artifact_relay.janitor import run_janitor
    from artifact_relay.storage import ArtifactStore

    store = ArtifactStore(tmp_path / "data")
    store.initialize()
    store.create(
        title="Протухший",
        summary=None,
        fmt="markdown",
        content=b"x",
        source_filename="s.md",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    from artifact_relay.db import connect

    def remaining() -> int:
        with connect(store.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"])

    async def drive() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run_janitor(store, interval_seconds=0.01, stop=stop))
        for _ in range(400):
            await asyncio.sleep(0.005)
            if remaining() == 0:
                break
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())

    assert remaining() == 0
    assert list(store.artifacts_dir.iterdir()) == []
