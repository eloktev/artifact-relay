"""SQLite metadata store.

One connection per operation. SQLite in WAL mode handles that comfortably for a single
replica and avoids any cross-thread connection sharing bugs with FastAPI's threadpool.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    summary         TEXT,
    format          TEXT NOT NULL CHECK (format IN ('markdown', 'html')),
    source_filename TEXT NOT NULL,
    content_bytes   INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    session_id      TEXT,
    session_title   TEXT,
    platform        TEXT,
    chat_name       TEXT,
    topic_id        TEXT,
    topic_name      TEXT,
    favorite        INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_artifacts_expires_at ON artifacts (expires_at);

CREATE TABLE IF NOT EXISTS assets (
    artifact_id TEXT NOT NULL REFERENCES artifacts (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    PRIMARY KEY (artifact_id, name)
);

CREATE TABLE IF NOT EXISTS share_links (
    id          TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts (id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    revoked_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_share_links_artifact ON share_links (artifact_id);

CREATE TABLE IF NOT EXISTS topic_aliases (
    platform   TEXT NOT NULL,
    chat_name  TEXT NOT NULL,
    topic_id   TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    PRIMARY KEY (platform, chat_name, topic_id)
);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(artifacts)")}
        migrations = {
            "session_id": "TEXT",
            "session_title": "TEXT",
            "platform": "TEXT",
            "chat_name": "TEXT",
            "topic_id": "TEXT",
            "topic_name": "TEXT",
            "favorite": "INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1))",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE artifacts ADD COLUMN {name} {declaration}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, isolation_level=None, timeout=15.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        yield conn
    finally:
        conn.close()
