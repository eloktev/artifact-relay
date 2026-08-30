"""Artifact persistence: SQLite metadata + immutable content on the filesystem.

Layout under ``DATA_DIR``::

    artifacts.db          metadata
    artifacts/<id>/source original bytes, exactly as published
    artifacts/<id>/assets/ optional attachments
    tmp/                  staging area for atomic publishes (same filesystem)

Write order matters: content is staged in ``tmp/`` and moved into place with a single
``os.replace`` *before* the metadata row is inserted. A crash therefore leaves at worst an
orphan directory (removed by the janitor), never a metadata row pointing at missing bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from artifact_relay.db import connect, init_db
from artifact_relay.models import Artifact, ArtifactFormat, Asset, ShareLink

# 24 bytes = 192 bits of entropy, comfortably above the 128-bit floor in the brief.
ID_BYTES = 24
SOURCE_FILENAME = "source"
# 24 url-safe base64 bytes render as exactly 32 characters; the range leaves room for a
# future change of ID_BYTES without loosening the guard.
ARTIFACT_ID = re.compile(r"[A-Za-z0-9_-]{22,64}")
ASSETS_DIRNAME = "assets"


def is_valid_artifact_id(value: str) -> bool:
    """Gate every filesystem and SQL path.

    Without this, an id of ".." turns ``artifact_dir()`` into the data directory itself
    and ``delete()`` into ``rmtree(DATA_DIR)``.
    """
    return bool(value) and bool(ARTIFACT_ID.fullmatch(value))


def new_artifact_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file(path: Path, payload: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


class ArtifactStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "artifacts.db"
        self.artifacts_dir = self.data_dir / "artifacts"
        self.tmp_dir = self.data_dir / "tmp"

    # -- lifecycle ---------------------------------------------------------
    def initialize(self) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.db_path)

    # -- paths -------------------------------------------------------------
    def artifact_dir(self, artifact_id: str) -> Path:
        if not is_valid_artifact_id(artifact_id):
            raise ValueError(f"invalid artifact id: {artifact_id[:32]!r}")
        return self.artifacts_dir / artifact_id

    def source_path(self, artifact_id: str) -> Path:
        return self.artifact_dir(artifact_id) / SOURCE_FILENAME

    def asset_path(self, artifact_id: str, name: str) -> Path:
        return self.artifact_dir(artifact_id) / ASSETS_DIRNAME / name

    # -- commands ----------------------------------------------------------
    def create(
        self,
        *,
        title: str,
        summary: str | None,
        fmt: ArtifactFormat,
        content: bytes,
        source_filename: str,
        expires_at: datetime | None,
        assets: Iterable[tuple[str, bytes, str]] = (),
        created_at: datetime | None = None,
        session_id: str | None = None,
        session_title: str | None = None,
        platform: str | None = None,
        chat_name: str | None = None,
        topic_id: str | None = None,
        topic_name: str | None = None,
    ) -> Artifact:
        artifact_id = new_artifact_id()
        created = created_at or datetime.now(UTC)
        asset_rows: list[Asset] = []

        staging = self.tmp_dir / f"stage-{secrets.token_hex(8)}"
        staging.mkdir(parents=True)
        try:
            _write_file(staging / SOURCE_FILENAME, content)
            materialised = list(assets)
            if materialised:
                assets_dir = staging / ASSETS_DIRNAME
                assets_dir.mkdir()
                for name, blob, media_type in materialised:
                    target = assets_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_file(target, blob)
                    asset_rows.append(Asset(name=name, media_type=media_type, size_bytes=len(blob)))
                _fsync_dir(assets_dir)
            _fsync_dir(staging)
            os.replace(staging, self.artifact_dir(artifact_id))
            _fsync_dir(self.artifacts_dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        artifact = Artifact(
            id=artifact_id,
            title=title,
            summary=summary,
            format=fmt,
            source_filename=source_filename,
            content_bytes=len(content),
            created_at=created,
            expires_at=expires_at,
            session_id=session_id,
            session_title=session_title,
            platform=platform,
            chat_name=chat_name,
            topic_id=topic_id,
            topic_name=topic_name,
            favorite=False,
        )
        try:
            with connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO artifacts (id, title, summary, format, source_filename,"
                    " content_bytes, created_at, expires_at, session_id, session_title,"
                    " platform, chat_name, topic_id, topic_name, favorite)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact.id,
                        artifact.title,
                        artifact.summary,
                        artifact.format,
                        artifact.source_filename,
                        artifact.content_bytes,
                        _iso(artifact.created_at),
                        _iso(artifact.expires_at) if artifact.expires_at else None,
                        artifact.session_id,
                        artifact.session_title,
                        artifact.platform,
                        artifact.chat_name,
                        artifact.topic_id,
                        artifact.topic_name,
                        int(artifact.favorite),
                    ),
                )
                conn.executemany(
                    "INSERT INTO assets (artifact_id, name, media_type, size_bytes)"
                    " VALUES (?, ?, ?, ?)",
                    [(artifact.id, a.name, a.media_type, a.size_bytes) for a in asset_rows],
                )
                conn.execute("COMMIT")
        except BaseException:
            shutil.rmtree(self.artifact_dir(artifact_id), ignore_errors=True)
            raise
        return artifact

    def delete(self, artifact_id: str) -> bool:
        """Remove metadata then bytes. Returns ``False`` if the artifact was already gone."""
        if not is_valid_artifact_id(artifact_id):
            return False
        with connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            removed = cursor.rowcount > 0
        shutil.rmtree(self.artifact_dir(artifact_id), ignore_errors=True)
        return removed

    def toggle_favorite(self, artifact_id: str) -> bool | None:
        """Flip favorite state and return it, or ``None`` for an unknown id."""
        if not is_valid_artifact_id(artifact_id):
            return None
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT favorite FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            favorite = not bool(row["favorite"])
            conn.execute(
                "UPDATE artifacts SET favorite = ? WHERE id = ?",
                (int(favorite), artifact_id),
            )
            conn.execute("COMMIT")
        return favorite

    def update_provenance(
        self,
        artifact_id: str,
        *,
        session_id: str | None,
        session_title: str | None,
        platform: str | None,
        chat_name: str | None,
        topic_id: str | None,
        topic_name: str | None,
    ) -> Artifact | None:
        if not is_valid_artifact_id(artifact_id):
            return None
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE artifacts SET session_id = ?, session_title = ?, platform = ?,"
                " chat_name = ?, topic_id = ?, topic_name = ? WHERE id = ?",
                (
                    session_id,
                    session_title,
                    platform,
                    chat_name,
                    topic_id,
                    topic_name,
                    artifact_id,
                ),
            )
        return self.get(artifact_id) if cursor.rowcount else None

    def create_share(
        self,
        artifact_id: str,
        *,
        expires_at: datetime | None,
        created_at: datetime | None = None,
    ) -> tuple[ShareLink, str]:
        if self.get(artifact_id) is None:
            raise ValueError("unknown artifact")
        token = secrets.token_urlsafe(32)
        created = created_at or datetime.now(UTC)
        share = ShareLink(
            id=secrets.token_urlsafe(12),
            artifact_id=artifact_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_at=created,
            expires_at=expires_at,
            revoked_at=None,
        )
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO share_links"
                " (id, artifact_id, token_hash, created_at, expires_at, revoked_at)"
                " VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    share.id,
                    share.artifact_id,
                    share.token_hash,
                    _iso(share.created_at),
                    _iso(share.expires_at) if share.expires_at else None,
                ),
            )
        return share, token

    def authorize_share(
        self,
        share_id: str,
        token: str,
        *,
        artifact_id: str | None = None,
        now: datetime | None = None,
    ) -> ShareLink | None:
        share = self.get_share(share_id)
        if share is None or not share.is_active(now or datetime.now(UTC)):
            return None
        if artifact_id is not None and share.artifact_id != artifact_id:
            return None
        candidate = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(candidate, share.token_hash):
            return None
        return share

    def revoke_share(self, share_id: str, *, revoked_at: datetime | None = None) -> bool:
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE share_links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_iso(revoked_at or datetime.now(UTC)), share_id),
            )
        return bool(cursor.rowcount)

    # -- queries -----------------------------------------------------------
    def get(self, artifact_id: str) -> Artifact | None:
        if not is_valid_artifact_id(artifact_id):
            return None
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._row_to_artifact(row) if row else None

    def list_live(self, now: datetime | None = None) -> list[Artifact]:
        """Return every non-expired artifact, favorites first within recency."""
        moment = _iso(now or datetime.now(UTC))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts"
                " WHERE expires_at IS NULL OR expires_at > ?"
                " ORDER BY favorite DESC, created_at DESC",
                (moment,),
            ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def get_share(self, share_id: str) -> ShareLink | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM share_links WHERE id = ?", (share_id,)).fetchone()
        if row is None:
            return None
        return ShareLink(
            id=row["id"],
            artifact_id=row["artifact_id"],
            token_hash=row["token_hash"],
            created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse(row["expires_at"]),
            revoked_at=_parse(row["revoked_at"]),
        )

    def list_shares(self, artifact_id: str) -> list[ShareLink]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM share_links WHERE artifact_id = ? ORDER BY created_at DESC",
                (artifact_id,),
            ).fetchall()
        return [
            ShareLink(
                id=row["id"],
                artifact_id=row["artifact_id"],
                token_hash=row["token_hash"],
                created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
                expires_at=_parse(row["expires_at"]),
                revoked_at=_parse(row["revoked_at"]),
            )
            for row in rows
        ]

    def list_assets(self, artifact_id: str) -> list[Asset]:
        if not is_valid_artifact_id(artifact_id):
            return []
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT name, media_type, size_bytes FROM assets WHERE artifact_id = ?"
                " ORDER BY name",
                (artifact_id,),
            ).fetchall()
        return [Asset(r["name"], r["media_type"], r["size_bytes"]) for r in rows]

    def get_asset(self, artifact_id: str, name: str) -> Asset | None:
        if not is_valid_artifact_id(artifact_id):
            return None
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name, media_type, size_bytes FROM assets"
                " WHERE artifact_id = ? AND name = ?",
                (artifact_id, name),
            ).fetchone()
        return Asset(row["name"], row["media_type"], row["size_bytes"]) if row else None

    def expired_ids(self, now: datetime) -> list[str]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM artifacts WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_iso(now),),
            ).fetchall()
        return [row["id"] for row in rows]

    def known_ids(self) -> set[str]:
        with connect(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM artifacts").fetchall()
        return {row["id"] for row in rows}

    def read_source(self, artifact_id: str) -> bytes:
        return self.source_path(artifact_id).read_bytes()

    @staticmethod
    def _row_to_artifact(row: object) -> Artifact:
        mapping = dict(row)  # type: ignore[call-overload]
        return Artifact(
            id=mapping["id"],
            title=mapping["title"],
            summary=mapping["summary"],
            format=mapping["format"],
            source_filename=mapping["source_filename"],
            content_bytes=mapping["content_bytes"],
            created_at=_parse(mapping["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse(mapping["expires_at"]),
            session_id=mapping["session_id"],
            session_title=mapping["session_title"],
            platform=mapping["platform"],
            chat_name=mapping["chat_name"],
            topic_id=mapping["topic_id"],
            topic_name=mapping["topic_name"],
            favorite=bool(mapping["favorite"]),
        )
