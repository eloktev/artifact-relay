from __future__ import annotations

import importlib.util
import io
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from artifact_relay.db import SCHEMA, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "restore_archive.py"


def run_tool(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, str(TOOL), *(str(arg) for arg in args)],
        text=True,
        capture_output=True,
        check=False,
    )


def make_archive(
    path: Path,
    *,
    malicious: bool = False,
    compatible: bool = True,
    replacement_index_sql: str | None = None,
) -> None:
    db = path.with_suffix(".db")
    with sqlite3.connect(db) as connection:
        if compatible:
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if replacement_index_sql:
                connection.execute("DROP INDEX idx_artifacts_expires_at")
                connection.execute(replacement_index_sql)
            connection.execute(
                "INSERT INTO artifacts "
                "(id, title, format, source_filename, content_bytes, created_at) "
                "VALUES ('restored', 'Restored', 'markdown', 'source.md', 13, "
                "'2026-08-30T00:00:00Z')"
            )
        else:
            connection.execute("CREATE TABLE artifacts (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO artifacts VALUES ('restored')")
    with tarfile.open(path, "w:gz") as archive:
        archive.add(db, arcname="artifacts.db")
        body = b"restored body"
        info = tarfile.TarInfo("artifacts/restored/source.md")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
        if malicious:
            payload = b"escape"
            bad = tarfile.TarInfo("../escaped")
            bad.size = len(payload)
            archive.addfile(bad, io.BytesIO(payload))
    db.unlink()


def test_validate_accepts_expected_archive(tmp_path: Path):
    archive = tmp_path / "valid.tar.gz"
    make_archive(archive)

    result = run_tool("validate", archive)

    assert result.returncode == 0, result.stderr


def test_validate_rejects_traversal_before_touching_data(tmp_path: Path):
    archive = tmp_path / "malicious.tar.gz"
    make_archive(archive, malicious=True)
    data = tmp_path / "data"
    data.mkdir()
    marker = data / "artifacts.db"
    marker.write_bytes(b"live database")

    result = run_tool("apply", archive, data)

    assert result.returncode != 0
    assert marker.read_bytes() == b"live database"
    assert not (tmp_path / "escaped").exists()


def test_validate_rejects_integrity_valid_but_incompatible_schema(tmp_path: Path):
    archive = tmp_path / "incompatible.tar.gz"
    make_archive(archive, compatible=False)

    result = run_tool("validate", archive)

    assert result.returncode != 0
    assert "schema" in result.stderr


@pytest.mark.parametrize(
    "replacement_index_sql",
    [
        "CREATE INDEX idx_artifacts_expires_at ON artifacts(title)",
        "CREATE UNIQUE INDEX idx_artifacts_expires_at ON artifacts(expires_at)",
    ],
)
def test_validate_rejects_wrong_index_definition(tmp_path: Path, replacement_index_sql: str):
    archive = tmp_path / "wrong-index.tar.gz"
    make_archive(archive, replacement_index_sql=replacement_index_sql)

    result = run_tool("validate", archive)

    assert result.returncode != 0
    assert "schema" in result.stderr


def test_apply_refuses_to_delete_preexisting_recovery_data(tmp_path: Path):
    archive = tmp_path / "valid.tar.gz"
    make_archive(archive)
    data = tmp_path / "data"
    recovery = data / ".restore-previous"
    recovery.mkdir(parents=True)
    marker = recovery / "artifacts.db"
    marker.write_bytes(b"recoverable")

    result = run_tool("apply", archive, data)

    assert result.returncode != 0
    assert marker.read_bytes() == b"recoverable"


def test_apply_replaces_data_only_after_full_validation(tmp_path: Path):
    archive = tmp_path / "valid.tar.gz"
    make_archive(archive)
    data = tmp_path / "data"
    (data / "artifacts" / "old").mkdir(parents=True)
    (data / "artifacts" / "old" / "source.md").write_text("old")
    (data / "artifacts.db").write_bytes(b"old database")

    result = run_tool("apply", archive, data)

    assert result.returncode == 0, result.stderr
    assert not (data / "artifacts" / "old").exists()
    assert (data / "artifacts" / "restored" / "source.md").read_text() == "restored body"
    with sqlite3.connect(data / "artifacts.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT id FROM artifacts").fetchone() == ("restored",)
    assert not (data / ".restore-staging").exists()
    assert (data / ".restore-previous" / "artifacts.db").read_bytes() == b"old database"

    committed = run_tool("commit", data)
    assert committed.returncode == 0, committed.stderr
    assert not (data / ".restore-previous").exists()


def test_staging_cleanup_failure_after_swap_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    spec = importlib.util.spec_from_file_location("restore_archive_cleanup_module", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = tmp_path / "valid.tar.gz"
    make_archive(archive)
    data = tmp_path / "data"
    data.mkdir()
    (data / "artifacts.db").write_bytes(b"old database")

    original_remove = module._remove
    staging_removals = 0

    def fail_second_staging_cleanup(path: Path):
        nonlocal staging_removals
        if path.name == ".restore-staging":
            staging_removals += 1
            if staging_removals == 2:
                raise OSError("injected staging cleanup failure")
        return original_remove(path)

    monkeypatch.setattr(module, "_remove", fail_second_staging_cleanup)
    module.apply(archive, data)

    with sqlite3.connect(data / "artifacts.db") as connection:
        assert connection.execute("SELECT id FROM artifacts").fetchone() == ("restored",)
    assert (data / ".restore-previous" / ".restore-manifest.json").is_file()


def test_partial_commit_cleanup_never_reopens_rollback_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    spec = importlib.util.spec_from_file_location("restore_archive_commit_module", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "data"
    previous = data / ".restore-previous"
    (previous / "artifacts").mkdir(parents=True)
    (previous / "artifacts.db").write_bytes(b"old database")
    (previous / ".restore-manifest.json").write_text('{"original": ["artifacts", "artifacts.db"]}')
    (data / "artifacts").mkdir()
    (data / "artifacts.db").write_bytes(b"new database")

    original_remove = module._remove

    def partially_delete_committed_garbage(path: Path):
        if path.name.startswith(".restore-committed-"):
            (path / "artifacts.db").unlink()
            raise OSError("injected garbage cleanup failure")
        return original_remove(path)

    monkeypatch.setattr(module, "_remove", partially_delete_committed_garbage)
    module.commit(data)
    module.rollback(data)

    assert not previous.exists()
    assert (data / "artifacts.db").read_bytes() == b"new database"
    assert any(data.glob(".restore-committed-*"))


def test_partial_rollback_cleanup_never_blocks_recovered_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    spec = importlib.util.spec_from_file_location("restore_archive_rollback_cleanup", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "data"
    previous = data / ".restore-previous"
    (previous / "artifacts").mkdir(parents=True)
    (previous / "artifacts" / "old.md").write_text("old")
    (previous / "artifacts.db").write_bytes(b"old database")
    (previous / ".restore-manifest.json").write_text('{"original": ["artifacts", "artifacts.db"]}')
    (data / "artifacts").mkdir()
    (data / "artifacts.db").write_bytes(b"new database")

    original_remove = module._remove

    def fail_rolled_back_garbage_cleanup(path: Path):
        if path.name.startswith(".restore-rolled-back-"):
            (path / ".restore-manifest.json").unlink()
            raise OSError("injected rollback garbage cleanup failure")
        return original_remove(path)

    monkeypatch.setattr(module, "_remove", fail_rolled_back_garbage_cleanup)
    module.rollback(data)
    module.rollback(data)

    assert not previous.exists()
    assert (data / "artifacts.db").read_bytes() == b"old database"
    assert (data / "artifacts" / "old.md").read_text() == "old"
    assert any(data.glob(".restore-rolled-back-*"))


def test_partial_rollback_failure_is_safe_to_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("restore_archive_test_module", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    data = tmp_path / "data"
    previous = data / ".restore-previous"
    (previous / "artifacts" / "old").mkdir(parents=True)
    (previous / "artifacts" / "old" / "source.md").write_text("old artifact")
    (previous / "artifacts.db").write_bytes(b"old database")
    (previous / ".restore-manifest.json").write_text('{"original": ["artifacts", "artifacts.db"]}')
    (data / "artifacts" / "new").mkdir(parents=True)
    (data / "artifacts.db").write_bytes(b"new database")

    original_rename = Path.rename
    failed = False

    def fail_database_rename_once(path: Path, target: Path):
        nonlocal failed
        if path == previous / "artifacts.db" and not failed:
            failed = True
            raise OSError("injected rename failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_database_rename_once)
    with pytest.raises(OSError, match="injected rename failure"):
        module.rollback(data)
    assert (data / "artifacts" / "old" / "source.md").read_text() == "old artifact"
    assert (previous / "artifacts.db").read_bytes() == b"old database"

    monkeypatch.setattr(Path, "rename", original_rename)
    module.rollback(data)

    assert (data / "artifacts" / "old" / "source.md").read_text() == "old artifact"
    assert (data / "artifacts.db").read_bytes() == b"old database"
    assert not previous.exists()


def test_rollback_restores_previous_data_after_failed_health_check(tmp_path: Path):
    archive = tmp_path / "valid.tar.gz"
    make_archive(archive)
    data = tmp_path / "data"
    (data / "artifacts" / "old").mkdir(parents=True)
    (data / "artifacts" / "old" / "source.md").write_text("old")
    (data / "artifacts.db").write_bytes(b"old database")
    assert run_tool("apply", archive, data).returncode == 0

    rolled_back = run_tool("rollback", data)

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert (data / "artifacts.db").read_bytes() == b"old database"
    assert (data / "artifacts" / "old" / "source.md").read_text() == "old"
    assert not (data / ".restore-previous").exists()
