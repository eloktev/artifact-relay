"""Application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from artifact_relay.capability import EmbedCapability, ShareEmbedCapability
from artifact_relay.config import Settings
from artifact_relay.errors import register as register_error_pages
from artifact_relay.janitor import run_janitor, startup_sweep
from artifact_relay.middleware import (
    AccessLogMiddleware,
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
    ShareLinksModeMiddleware,
)
from artifact_relay.ratelimit import FixedWindowRateLimiter
from artifact_relay.routers import api, auth, viewer
from artifact_relay.security import PasswordVerificationGate, SessionSigner, ShareSessionSigner
from artifact_relay.storage import ArtifactStore
from artifact_relay.templating import STATIC_DIR
from artifact_relay.validation import max_request_bytes


def create_app(settings: Settings) -> FastAPI:
    store = ArtifactStore(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store.initialize()
        startup_sweep(store)
        stop = asyncio.Event()
        task = asyncio.create_task(run_janitor(store, settings.janitor_interval_seconds, stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Artifact Relay",
        version="1.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.store = store
    app.state.session_signer = SessionSigner(
        settings.session_secret_key.get_secret_value(),
        settings.session_ttl_days * 86400,
    )
    app.state.share_session_signer = ShareSessionSigner(
        settings.session_secret_key.get_secret_value(),
        settings.session_ttl_days * 86400,
    )
    # Added first => innermost, so a 413 raised on the receive channel still passes back out
    # through the security headers and the access log on its way to the client.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_request_bytes(settings))
    # Keep this outside the body-size guard so disabled share endpoints always disappear as
    # 404, even for malformed or oversized requests, while retaining headers and access logs.
    app.add_middleware(ShareLinksModeMiddleware, enabled=settings.share_links_enabled)
    # Added last => outermost, so the access log also covers responses produced by the
    # security-header middleware itself.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Signed by the same secret as the session cookie, under its own salt: rotating
    # SESSION_SECRET_KEY invalidates every outstanding iframe capability too.
    app.state.embed_capability = EmbedCapability(
        settings.session_secret_key.get_secret_value(),
        settings.embed_token_ttl_seconds,
    )
    app.state.share_embed_capability = ShareEmbedCapability(
        settings.session_secret_key.get_secret_value(),
        settings.embed_token_ttl_seconds,
    )
    app.state.login_limiter = FixedWindowRateLimiter(
        settings.login_max_attempts, settings.login_window_seconds
    )
    # Process-wide, deliberately not per-client: the resource being protected is the
    # allocator, and a distributed attempt flood is exactly the case a per-client limit misses.
    app.state.verification_gate = PasswordVerificationGate(
        settings.login_max_concurrent_verifications
    )
    app.include_router(api.router)
    app.include_router(auth.router)
    app.include_router(viewer.router)
    register_error_pages(app)
    return app
