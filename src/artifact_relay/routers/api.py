"""Publisher API (bearer authenticated)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from artifact_relay.assets import media_type_for
from artifact_relay.config import Settings
from artifact_relay.dependencies import (
    enforce_request_size,
    get_settings,
    get_store,
    require_api_token,
)
from artifact_relay.models import Artifact, ArtifactFormat
from artifact_relay.schemas import ArtifactReadResponse, ProvenanceUpdate, PublishResponse
from artifact_relay.storage import ArtifactStore
from artifact_relay.validation import (
    read_bounded,
    validate_asset_count,
    validate_assets,
    validate_content,
    validate_expires_in_days,
    validate_summary,
    validate_title,
)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _collect_assets(
    uploads: list[UploadFile] | None, settings: Settings
) -> list[tuple[str, bytes, str]]:
    """Read the attachments against a single shared byte budget.

    The count is checked first, so a thousand-part upload is refused before any of it is
    read; then each part draws from what is left of `max_asset_bytes`, which means the total
    is bounded as it arrives rather than summed up afterwards.
    """
    uploads = uploads or []
    validate_asset_count(len(uploads), settings)

    collected: list[tuple[str, bytes, str]] = []
    remaining = settings.max_asset_bytes
    detail = f"assets exceed {settings.max_asset_bytes} bytes in total"
    for upload in uploads:
        name = (upload.filename or "").strip()
        blob = await read_bounded(upload, remaining, detail)
        remaining -= len(blob)
        collected.append((name, blob, media_type_for(name)[0]))
    return collected


def _expiry(days: int, now: datetime) -> datetime | None:
    return None if days == 0 else now + timedelta(days=days)


def _load_live_artifact(store: ArtifactStore, artifact_id: str) -> Artifact:
    artifact = store.get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if artifact.is_expired(datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gone")
    return artifact


@router.post(
    "/artifacts",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_token), Depends(enforce_request_size)],
    summary="Publish an artifact",
)
async def publish_artifact(
    request: Request,
    title: Annotated[str, Form()],
    fmt: Annotated[ArtifactFormat, Form(alias="format")],
    content: Annotated[UploadFile, File()],
    summary: Annotated[str | None, Form()] = None,
    expires_in_days: Annotated[int | None, Form()] = None,
    assets: Annotated[list[UploadFile] | None, File()] = None,
    session_id: Annotated[str | None, Form(max_length=512)] = None,
    session_title: Annotated[str | None, Form(max_length=512)] = None,
    platform: Annotated[str | None, Form(max_length=512)] = None,
    chat_name: Annotated[str | None, Form(max_length=512)] = None,
    topic_id: Annotated[str | None, Form(max_length=512)] = None,
    topic_name: Annotated[str | None, Form(max_length=512)] = None,
) -> PublishResponse:
    settings: Settings = get_settings(request)
    store: ArtifactStore = get_store(request)

    clean_title = validate_title(title, settings)
    clean_summary = validate_summary(summary, settings)
    days = validate_expires_in_days(expires_in_days, settings)
    payload = validate_content(
        await read_bounded(
            content,
            settings.max_content_bytes,
            f"content exceeds {settings.max_content_bytes} bytes",
        ),
        settings,
    )
    collected_assets = validate_assets(await _collect_assets(assets, settings), settings)
    now = datetime.now(UTC)
    artifact = store.create(
        title=clean_title,
        summary=clean_summary,
        fmt=fmt,
        content=payload,
        source_filename=content.filename or "source",
        expires_at=_expiry(days, now),
        assets=collected_assets,
        created_at=now,
        session_id=session_id,
        session_title=session_title,
        platform=platform,
        chat_name=chat_name,
        topic_id=topic_id,
        topic_name=topic_name,
    )
    return PublishResponse(
        id=artifact.id,
        url=settings.absolute_url(f"/a/{artifact.id}"),
        title=artifact.title,
        summary=artifact.summary,
        format=artifact.format,
        created_at=artifact.created_at,
        expires_at=artifact.expires_at,
        asset_names=[name for name, _, _ in collected_assets],
        session_id=artifact.session_id,
        session_title=artifact.session_title,
        platform=artifact.platform,
        chat_name=artifact.chat_name,
        topic_id=artifact.topic_id,
        topic_name=artifact.topic_name,
        favorite=artifact.favorite,
    )


@router.get(
    "/artifacts/{artifact_id}",
    dependencies=[Depends(require_api_token)],
    summary="Read an artifact for a trusted agent",
)
def read_artifact(request: Request, artifact_id: str) -> ArtifactReadResponse:
    store = get_store(request)
    artifact = _load_live_artifact(store, artifact_id)
    return ArtifactReadResponse(
        id=artifact.id,
        url=get_settings(request).absolute_url(f"/a/{artifact.id}"),
        title=artifact.title,
        summary=artifact.summary,
        format=artifact.format,
        created_at=artifact.created_at,
        expires_at=artifact.expires_at,
        asset_names=[asset.name for asset in store.list_assets(artifact.id)],
        source_filename=artifact.source_filename,
        content=store.read_source(artifact.id).decode("utf-8"),
        session_id=artifact.session_id,
        session_title=artifact.session_title,
        platform=artifact.platform,
        chat_name=artifact.chat_name,
        topic_id=artifact.topic_id,
        topic_name=artifact.topic_name,
        favorite=artifact.favorite,
    )


@router.patch(
    "/artifacts/{artifact_id}/provenance",
    dependencies=[Depends(require_api_token)],
    summary="Backfill an artifact's Hermes session and topic provenance",
)
def update_provenance(
    request: Request, artifact_id: str, update: ProvenanceUpdate
) -> PublishResponse:
    store = get_store(request)
    _load_live_artifact(store, artifact_id)
    artifact = store.update_provenance(artifact_id, **update.model_dump())
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return PublishResponse(
        id=artifact.id,
        url=get_settings(request).absolute_url(f"/a/{artifact.id}"),
        title=artifact.title,
        summary=artifact.summary,
        format=artifact.format,
        created_at=artifact.created_at,
        expires_at=artifact.expires_at,
        asset_names=[asset.name for asset in store.list_assets(artifact.id)],
        session_id=artifact.session_id,
        session_title=artifact.session_title,
        platform=artifact.platform,
        chat_name=artifact.chat_name,
        topic_id=artifact.topic_id,
        topic_name=artifact.topic_name,
        favorite=artifact.favorite,
    )


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_token)],
    summary="Delete an artifact and every byte belonging to it",
)
def delete_artifact(request: Request, artifact_id: str) -> Response:
    if not get_store(request).delete(artifact_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
