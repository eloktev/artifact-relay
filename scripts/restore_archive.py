#!/usr/bin/env python3
"""Validate and transactionally apply Artifact Relay backup archives."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path, PurePosixPath

from artifact_relay.db import SCHEMA_VERSION

DB_NAME = "artifacts.db"
DATA_NAMES = ("artifacts", "tmp", DB_NAME, f"{DB_NAME}-wal", f"{DB_NAME}-shm")


class ArchiveError(ValueError):
    """The backup is unsafe, corrupt, or incompatible."""


def _members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    database_members = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ArchiveError(f"unsafe archive path: {member.name}")
        if path.parts[0] == DB_NAME:
            if len(path.parts) != 1 or not member.isfile():
                raise ArchiveError("artifacts.db must be one regular file")
            database_members += 1
        elif path.parts[0] == "artifacts":
            if not (member.isfile() or member.isdir()):
                raise ArchiveError(f"unsupported archive member type: {member.name}")
        else:
            raise ArchiveError(f"unexpected archive member: {member.name}")
    if database_members != 1:
        raise ArchiveError("archive must contain exactly one artifacts.db")
    return members


EXPECTED_COLUMNS = {
    "artifacts": [
        ("id", "TEXT", 0, 1),
        ("title", "TEXT", 1, 0),
        ("summary", "TEXT", 0, 0),
        ("format", "TEXT", 1, 0),
        ("source_filename", "TEXT", 1, 0),
        ("content_bytes", "INTEGER", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 0, 0),
        ("session_id", "TEXT", 0, 0),
        ("session_title", "TEXT", 0, 0),
        ("platform", "TEXT", 0, 0),
        ("chat_name", "TEXT", 0, 0),
        ("topic_id", "TEXT", 0, 0),
        ("topic_name", "TEXT", 0, 0),
        ("favorite", "INTEGER", 1, 0),
    ],
    "assets": [
        ("artifact_id", "TEXT", 1, 1),
        ("name", "TEXT", 1, 2),
        ("media_type", "TEXT", 1, 0),
        ("size_bytes", "INTEGER", 1, 0),
    ],
    "share_links": [
        ("id", "TEXT", 0, 1),
        ("artifact_id", "TEXT", 1, 0),
        ("token_hash", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 0, 0),
        ("revoked_at", "TEXT", 0, 0),
    ],
}


def _validate_database(path: Path) -> None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            schemas = {
                name: connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
                ).fetchone()
                for name in EXPECTED_COLUMNS
            }
            columns = {
                name: [
                    (row[1], row[2].upper(), row[3], row[5])
                    for row in connection.execute(f"PRAGMA table_info({name})")
                ]
                for name in EXPECTED_COLUMNS
            }
            required_indexes = {
                "idx_artifacts_expires_at": ("artifacts", False, False, ("expires_at",)),
                "idx_share_links_artifact": ("share_links", False, False, ("artifact_id",)),
            }
            indexes = {}
            for name, (table, _unique, _partial, _columns) in required_indexes.items():
                matching = [
                    row
                    for row in connection.execute(f"PRAGMA index_list({table})")
                    if row[1] == name
                ]
                if matching:
                    row = matching[0]
                    indexes[name] = (
                        table,
                        bool(row[2]),
                        bool(row[4]),
                        tuple(info[2] for info in connection.execute(f"PRAGMA index_info({name})")),
                    )
            foreign_keys = {
                table: {
                    (row[2], row[3], row[4], row[6].upper())
                    for row in connection.execute(f"PRAGMA foreign_key_list({table})")
                }
                for table in ("assets", "share_links")
            }
    except sqlite3.Error as exc:
        raise ArchiveError("archive contains an invalid SQLite database") from exc

    normalized_artifacts_sql = "".join((schemas["artifacts"] or ("",))[0].lower().split())
    schema_ok = (
        integrity == ("ok",)
        and version == SCHEMA_VERSION
        and all(schemas.values())
        and columns == EXPECTED_COLUMNS
        and indexes == required_indexes
        and foreign_keys["assets"] == {("artifacts", "artifact_id", "id", "CASCADE")}
        and foreign_keys["share_links"] == {("artifacts", "artifact_id", "id", "CASCADE")}
        and "check(formatin('markdown','html'))" in normalized_artifacts_sql
        and "check(favoritein(0,1))" in normalized_artifacts_sql
    )
    if not schema_ok:
        raise ArchiveError("archive database failed integrity or schema validation")


def validate(archive_path: Path) -> None:
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _members(archive)
            database = next(member for member in members if member.name == DB_NAME)
            with tempfile.TemporaryDirectory(prefix="artifact-restore-validate-") as directory:
                archive.extract(database, directory, filter="data")
                _validate_database(Path(directory) / DB_NAME)
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError("backup archive is unreadable or corrupt") from exc


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def ready(data_dir: Path) -> None:
    """Fail before downtime when unresolved recovery state exists."""
    previous = data_dir / ".restore-previous"
    if previous.exists():
        raise ArchiveError("recovery data already exists; rollback or commit it before retrying")
    _remove(data_dir / ".restore-staging")
    for garbage in data_dir.glob(".restore-*-*"):
        # Committed/rolled-back garbage is never a recovery source and cannot block a new
        # transaction.
        with suppress(OSError):
            _remove(garbage)


def apply(archive_path: Path, data_dir: Path) -> None:
    # Complete this preflight before touching the live data tree.
    validate(archive_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = data_dir / ".restore-staging"
    previous = data_dir / ".restore-previous"
    if previous.exists():
        raise ArchiveError("recovery data already exists; rollback or commit it before retrying")
    _remove(staging)
    staging.mkdir(mode=0o700)
    previous.mkdir(mode=0o700)

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(staging, members=_members(archive), filter="data")
        _validate_database(staging / DB_NAME)

        original = [name for name in DATA_NAMES if (data_dir / name).exists()]
        (previous / ".restore-manifest.json").write_text(
            json.dumps({"original": original}), encoding="utf-8"
        )
        try:
            for name in original:
                (data_dir / name).rename(previous / name)
            for name in (DB_NAME, "artifacts"):
                candidate = staging / name
                if candidate.exists():
                    candidate.rename(data_dir / name)
            (data_dir / "tmp").mkdir(exist_ok=True)
        except Exception:
            try:
                rollback(data_dir)
            except Exception as rollback_error:
                raise ArchiveError(
                    "restore swap failed and automatic rollback is incomplete"
                ) from rollback_error
            raise
    except Exception:
        _remove(staging)
        # If the swap never began, previous is empty. If rollback itself failed, retain it for
        # manual recovery rather than deleting the last known-good data.
        if previous.exists() and not any(previous.iterdir()):
            _remove(previous)
        raise
    else:
        # The live swap is complete and rollback data is intact. Staging is non-authoritative
        # garbage; reporting failure here would misclassify a pending transaction.
        with suppress(OSError):
            _remove(staging)


def commit(data_dir: Path) -> None:
    previous = data_dir / ".restore-previous"
    if not previous.exists():
        raise ArchiveError("no pending restore transaction to commit")
    garbage = data_dir / f".restore-committed-{uuid.uuid4().hex}"
    # This same-filesystem rename is the atomic boundary: after it succeeds, rollback must never
    # consume the tree, even if best-effort garbage deletion is interrupted or partially fails.
    previous.rename(garbage)
    with suppress(OSError):
        _remove(garbage)


def rollback(data_dir: Path) -> None:
    previous = data_dir / ".restore-previous"
    manifest_path = previous / ".restore-manifest.json"
    if not previous.exists():
        _remove(data_dir / ".restore-staging")
        return
    if not manifest_path.is_file():
        raise ArchiveError("recovery tree exists without a complete manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original = manifest["original"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ArchiveError("recovery manifest is invalid") from exc
    if not isinstance(original, list) or any(name not in DATA_NAMES for name in original):
        raise ArchiveError("recovery manifest contains invalid data names")

    original_names = set(original)
    for name in DATA_NAMES:
        source = previous / name
        destination = data_dir / name
        if name in original_names:
            # A missing source means a prior rollback attempt already restored this component.
            # Never delete that recovered destination on retry.
            if source.exists():
                _remove(destination)
                source.rename(destination)
        else:
            _remove(destination)
    garbage = data_dir / f".restore-rolled-back-{uuid.uuid4().hex}"
    previous.rename(garbage)
    with suppress(OSError):
        _remove(garbage)
    with suppress(OSError):
        _remove(data_dir / ".restore-staging")


def main(argv: list[str]) -> int:
    usage = (
        "usage: restore_archive.py validate ARCHIVE | ready DATA_DIR | apply ARCHIVE DATA_DIR | "
        "commit DATA_DIR | rollback DATA_DIR"
    )
    if len(argv) < 2 or argv[1] not in {"validate", "ready", "apply", "commit", "rollback"}:
        print(usage, file=sys.stderr)
        return 2
    command = argv[1]
    expected_length = 4 if command == "apply" else 3
    if len(argv) != expected_length:
        print(usage, file=sys.stderr)
        return 2
    try:
        if command == "validate":
            validate(Path(argv[2]).resolve())
        elif command == "ready":
            ready(Path(argv[2]).resolve())
        elif command == "apply":
            apply(Path(argv[2]).resolve(), Path(argv[3]).resolve())
        elif command == "commit":
            commit(Path(argv[2]).resolve())
        else:
            rollback(Path(argv[2]).resolve())
    except (ArchiveError, OSError, tarfile.TarError) as exc:
        print(f"restore refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
