"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from artifact_relay.capability import EmbedCapability, ShareEmbedCapability
from artifact_relay.config import Settings
from artifact_relay.ratelimit import FixedWindowRateLimiter
from artifact_relay.security import (
    PasswordVerificationGate,
    SessionSigner,
    ShareSessionSigner,
    verify_bearer_token,
)
from artifact_relay.storage import ArtifactStore


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_store(request: Request) -> ArtifactStore:
    store: ArtifactStore = request.app.state.store
    return store


def get_session_signer(request: Request) -> SessionSigner:
    signer: SessionSigner = request.app.state.session_signer
    return signer


def get_share_session_signer(request: Request) -> ShareSessionSigner:
    signer: ShareSessionSigner = request.app.state.share_session_signer
    return signer


def get_login_limiter(request: Request) -> FixedWindowRateLimiter:
    limiter: FixedWindowRateLimiter = request.app.state.login_limiter
    return limiter


def get_embed_capability(request: Request) -> EmbedCapability:
    capability: EmbedCapability = request.app.state.embed_capability
    return capability


def get_share_embed_capability(request: Request) -> ShareEmbedCapability:
    capability: ShareEmbedCapability = request.app.state.share_embed_capability
    return capability


def get_verification_gate(request: Request) -> PasswordVerificationGate:
    gate: PasswordVerificationGate = request.app.state.verification_gate
    return gate


def client_key(request: Request) -> str:
    """Identify the caller for throttling.

    ``request.client.host`` is the real peer address because uvicorn is started with
    ``--proxy-headers`` behind the reverse proxy; see README for the trusted-proxy note.
    """
    return request.client.host if request.client else "unknown"


def has_session(request: Request) -> bool:
    settings = get_settings(request)
    cookie = request.cookies.get(settings.session_cookie_name)
    return get_session_signer(request).verify(cookie)


def enforce_request_size(request: Request) -> None:
    """Reject an oversized upload from its Content-Length, before it is buffered."""
    from artifact_relay.validation import max_request_bytes

    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        return
    if int(declared) > max_request_bytes(get_settings(request)):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="request body too large",
        )


def require_session(request: Request) -> None:
    """Reject sub-resource requests that carry no viewer session."""
    if not has_session(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Authentication required")


def require_api_token(request: Request) -> None:
    """Reject the request unless it carries the publisher bearer token.

    Runs before the request body is parsed, so an unauthenticated caller can never make the
    server buffer a multi-megabyte upload.
    """
    settings = get_settings(request)
    if not verify_bearer_token(
        request.headers.get("authorization"),
        settings.artifact_api_token.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
