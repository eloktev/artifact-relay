"""Public API response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from artifact_relay.models import ArtifactFormat


class PublishResponse(BaseModel):
    id: str
    url: str
    title: str
    summary: str | None
    format: ArtifactFormat
    created_at: datetime
    expires_at: datetime | None
    asset_names: list[str] = []
    session_id: str | None = None
    session_title: str | None = None
    platform: str | None = None
    chat_name: str | None = None
    topic_id: str | None = None
    topic_name: str | None = None
    favorite: bool = False


class ArtifactReadResponse(PublishResponse):
    """Full artifact source for the trusted Hermes API client."""

    source_filename: str
    content: str


class ProvenanceUpdate(BaseModel):
    session_id: str | None = Field(default=None, max_length=512)
    session_title: str | None = Field(default=None, max_length=512)
    platform: str | None = Field(default=None, max_length=512)
    chat_name: str | None = Field(default=None, max_length=512)
    topic_id: str | None = Field(default=None, max_length=512)
    topic_name: str | None = Field(default=None, max_length=512)


class ShareRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
