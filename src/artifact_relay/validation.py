"""Publish-request validation.

Everything is checked *before* a single byte is written, so a rejected publish leaves no
directory, no temporary file and no metadata row behind.

Status codes: 413 when something is simply too big, 422 when it is malformed.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, status

from artifact_relay.assets import is_safe_asset_name
from artifact_relay.config import Settings

TOO_LARGE = status.HTTP_413_CONTENT_TOO_LARGE
INVALID = status.HTTP_422_UNPROCESSABLE_CONTENT

# Upper bound on a single read. The actual request is always the smaller of this and
# "one byte past the remaining budget", so an oversized part is never concatenated.
READ_CHUNK_BYTES = 64 * 1024


class BoundedReadable(Protocol):
    """The part of ``UploadFile`` this module needs — and it must take a size."""

    async def read(self, size: int = -1) -> bytes: ...


async def read_bounded(
    upload: BoundedReadable, limit: int, detail: str = "request body too large"
) -> bytes:
    """Read a part, refusing at ``limit + 1`` bytes without ever buffering more.

    ``await upload.read()`` with no argument — the obvious spelling — pulls the entire part
    into one ``bytes`` object before any limit can be consulted, so a caller who omits
    ``Content-Length`` (chunked transfer) chooses how much memory the process allocates. Here
    each read asks for at most one byte beyond what is still allowed, so the peak is the
    limit plus one byte regardless of how much the caller intends to send.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        wanted = min(READ_CHUNK_BYTES, limit - total + 1)
        chunk = await upload.read(wanted)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise _reject(TOO_LARGE, detail)
        chunks.append(chunk)


def _reject(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def validate_title(raw: str, settings: Settings) -> str:
    title = raw.strip()
    if not title:
        raise _reject(INVALID, "title must not be blank")
    if len(title) > settings.max_title_chars:
        raise _reject(INVALID, f"title exceeds {settings.max_title_chars} characters")
    return title


def validate_summary(raw: str | None, settings: Settings) -> str | None:
    if raw is None:
        return None
    summary = raw.strip()
    if not summary:
        return None
    if len(summary) > settings.max_summary_chars:
        raise _reject(INVALID, f"summary exceeds {settings.max_summary_chars} characters")
    return summary


def validate_content(payload: bytes, settings: Settings) -> bytes:
    if not payload:
        raise _reject(INVALID, "content must not be empty")
    if len(payload) > settings.max_content_bytes:
        raise _reject(TOO_LARGE, f"content exceeds {settings.max_content_bytes} bytes")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _reject(INVALID, "content must be valid UTF-8") from exc
    return payload


def validate_expires_in_days(raw: int | None, settings: Settings) -> int:
    days = settings.default_ttl_days if raw is None else raw
    if days < 0:
        raise _reject(INVALID, "expires_in_days must not be negative")
    if days > settings.max_ttl_days:
        raise _reject(INVALID, f"expires_in_days exceeds {settings.max_ttl_days}")
    return days


def validate_asset_count(count: int, settings: Settings) -> None:
    """Checked before a single asset byte is read, not after."""
    if count > settings.max_assets:
        raise _reject(TOO_LARGE, f"at most {settings.max_assets} assets are allowed")


def validate_assets(
    collected: list[tuple[str, bytes, str]], settings: Settings
) -> list[tuple[str, bytes, str]]:
    validate_asset_count(len(collected), settings)

    total = 0
    seen: set[str] = set()
    for name, blob, _ in collected:
        if not is_safe_asset_name(name):
            raise _reject(INVALID, f"unsafe asset filename: {name[:64]!r}")
        if name in seen:
            raise _reject(INVALID, f"duplicate asset filename: {name!r}")
        seen.add(name)
        total += len(blob)
    if total > settings.max_asset_bytes:
        raise _reject(TOO_LARGE, f"assets exceed {settings.max_asset_bytes} bytes in total")
    return collected


def max_request_bytes(settings: Settings) -> int:
    """Upper bound for the whole multipart envelope, with room for part headers."""
    return settings.max_content_bytes + settings.max_asset_bytes + 64 * 1024
