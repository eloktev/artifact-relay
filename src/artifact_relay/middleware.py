"""Baseline response hardening for every route the service serves."""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("artifact_relay.access")

ROBOTS = "noindex, nofollow, noarchive"

# Applies to the service's own pages. The artifact iframe document sets its own, far
# stricter, policy in routers/viewer.py and is exempted here.
VIEWER_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'none'",
        "frame-src 'self'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "object-src 'none'",
    )
)

# /embed/<artifact id>/<capability token>/... — the token is a credential, so the access log
# records the shape of the request without recording the credential itself.
_EMBED_TOKEN = re.compile(r"^(/embed/[^/]+/)[^/]+")
_SHARE_EMBED_TOKEN = re.compile(r"^(/s/[^/]+/embed/)[^/]+")
_SHARE_REDEEM_PATH = re.compile(r"^/s/[^/]+/redeem$")
_SHARE_CREATE_PATH = re.compile(r"^/a/[^/]+/shares$")
_SHARE_SURFACE_PATH = re.compile(r"^(?:/s(?:/|$)|/a/[^/]+/shares(?:/|$))")
SHARE_FORM_MAX_BYTES = 1024


def redact_path(path: str) -> str:
    return _SHARE_EMBED_TOKEN.sub(r"\1***", _EMBED_TOKEN.sub(r"\1***", path))


BASE_HEADERS = {
    "X-Robots-Tag": ROBOTS,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
}


class _BodyTooLarge(Exception):
    """Raised from the receive channel; never escapes this module."""


class MaxBodySizeMiddleware:
    """A ceiling on the bytes that actually arrive, not the bytes the caller declares.

    `Content-Length` is a claim, and a chunked request does not even make one. This is pure
    ASGI rather than `BaseHTTPMiddleware` because the count has to happen on the receive
    channel itself: by the time a request object exists the body has already been read.

    Stopping here also protects what the route cannot. Starlette's multipart parser drains
    the whole stream before the endpoint runs, spooling large parts to a temporary file, so
    without this an authenticated publisher could stream indefinitely and fill the disk no
    matter how carefully the endpoint then reads.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        is_small_share_body = scope.get("method") == "POST" and bool(
            _SHARE_REDEEM_PATH.fullmatch(path) or _SHARE_CREATE_PATH.fullmatch(path)
        )
        max_bytes = SHARE_FORM_MAX_BYTES if is_small_share_body else self.max_bytes
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length", b"")
        if is_small_share_body and declared.isdigit() and int(declared) > max_bytes:
            response = JSONResponse({"detail": "request body too large"}, status_code=413)
            await response(scope, receive, send)
            return
        received = 0
        exceeded = False
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    exceeded = True
                    # Stops the parser from pulling any more of the stream.
                    raise _BodyTooLarge
            return message

        async def watched_send(message: Message) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                # FastAPI turns *any* exception raised out of form parsing into a generic
                # 400, so the verdict cannot be left to whatever the app decided to say
                # about a body that was cut off deliberately. Drop it and answer below.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with suppress(_BodyTooLarge):
            await self.app(scope, bounded_receive, watched_send)

        if exceeded and not response_started:
            response_started = True
            response = JSONResponse({"detail": "request body too large"}, status_code=413)
            await response(scope, receive, send)


class ShareLinksModeMiddleware:
    """Make every sharing HTTP surface disappear when sharing is disabled."""

    def __init__(self, app: ASGIApp, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] == "http" and not self.enabled and _SHARE_SURFACE_PATH.match(path):
            response = JSONResponse({"detail": "Not found"}, status_code=404)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault("Content-Security-Policy", VIEWER_CSP)
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured line per request.

    Only method, path, status, duration and a random request id — deliberately not the query
    string, not headers, not the body.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = secrets.token_hex(8)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": redact_path(request.url.path),
                    "status": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        logger.info(
            "request",
            extra={
                "event": "request",
                "request_id": request_id,
                "method": request.method,
                "path": redact_path(request.url.path),
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        response.headers.setdefault("X-Request-Id", request_id)
        return response
