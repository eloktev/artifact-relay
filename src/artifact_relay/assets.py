"""Artifact attachment naming and media types.

Asset names are a **flat** namespace: no directories, no dot-prefixed names, ASCII only.
Rejecting rather than "sanitising" a hostile name is deliberate — silently rewriting
``../../etc/passwd`` into ``etcpasswd`` hides a bug from the publisher and invites a second
sanitiser somewhere else to disagree with this one.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

MAX_NAME_LENGTH = 120
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Types a browser may render in place. Everything else is forced to a download so that an
# attacker-supplied `.html` or `.js` can never execute on this service's origin.
INLINE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}
DOWNLOAD_MEDIA_TYPE = "application/octet-stream"

# Assets are served with this policy so that a direct navigation to, say, an SVG cannot
# execute script on the service origin even though <img src> rendering still works.
ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox; base-uri 'none'"


def is_safe_asset_name(name: str) -> bool:
    if not name or len(name) > MAX_NAME_LENGTH:
        return False
    if not name.isascii() or not _SAFE_NAME.fullmatch(name):
        return False
    if ".." in name or name in {".", ".."}:
        return False
    # Belt and braces: the name must be a single, non-special path component.
    parts = PurePosixPath(name).parts
    return len(parts) == 1 and parts[0] == name


def media_type_for(name: str) -> tuple[str, bool]:
    """Return ``(content_type, render_inline)`` for an asset name."""
    suffix = PurePosixPath(name).suffix.lower()
    inline = INLINE_MEDIA_TYPES.get(suffix)
    if inline is None:
        return DOWNLOAD_MEDIA_TYPE, False
    return inline, True
