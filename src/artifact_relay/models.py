"""Domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ArtifactFormat = Literal["markdown", "html"]


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ShareLink:
    id: str
    artifact_id: str
    token_hash: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and (self.expires_at is None or now < self.expires_at)


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    title: str
    summary: str | None
    format: ArtifactFormat
    source_filename: str
    content_bytes: int
    created_at: datetime
    expires_at: datetime | None
    session_id: str | None = None
    session_title: str | None = None
    platform: str | None = None
    chat_name: str | None = None
    topic_id: str | None = None
    topic_name: str | None = None
    favorite: bool = False

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at

    @property
    def is_pinned(self) -> bool:
        return self.expires_at is None
