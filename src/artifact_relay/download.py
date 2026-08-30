"""Filenames and media types for the "download the source" control."""

from __future__ import annotations

import re
from urllib.parse import quote

from artifact_relay.models import Artifact, ArtifactFormat

EXTENSIONS: dict[str, str] = {"markdown": "md", "html": "html"}

# An artifact's own source is always downloaded, never rendered: serving a published
# `text/html` body inline from this origin would defeat the whole iframe sandbox.
SOURCE_MEDIA_TYPES: dict[str, str] = {
    "markdown": "text/markdown; charset=utf-8",
    "html": "text/plain; charset=utf-8",
}

# Everything outside this set collapses to a single "-", so quotes, control characters and
# path separators are structurally impossible in the fallback name.
_ASCII_FALLBACK = re.compile(r"[^A-Za-z0-9._-]+")
MAX_ASCII_STEM = 60


def source_media_type(fmt: ArtifactFormat) -> str:
    return SOURCE_MEDIA_TYPES[fmt]


def download_filename(artifact: Artifact) -> str:
    extension = EXTENSIONS[artifact.format]
    stem = artifact.title.strip()[:60].strip() or "artifact"
    return f"{stem}.{extension}"


def ascii_fallback_filename(artifact: Artifact) -> str:
    """The plain `filename=` for clients that do not understand `filename*=`.

    Titles here are usually entirely Cyrillic, and transliterating them is not this service's
    job — but substituting over the whole `<title>.<ext>` string left `-.md`, which strips
    down to the literal `.md`: a dotfile, hidden on every Unix client and refused by some
    Windows ones. So the substitution runs over the *title alone*, the extension is appended
    once afterwards, and a stem with no alphanumeric character left in it falls back to the
    artifact id — unique, traceable, and unmistakably a name.
    """
    extension = EXTENSIONS[artifact.format]
    stem = _ASCII_FALLBACK.sub("-", artifact.title.strip()[:MAX_ASCII_STEM]).strip("-._")
    if not any(character.isalnum() for character in stem):
        return f"artifact-{artifact.id}.{extension}"
    return f"{stem}.{extension}"


def content_disposition(artifact: Artifact) -> str:
    """RFC 6266 header with an ASCII fallback plus a UTF-8 variant for real filenames."""
    encoded = quote(download_filename(artifact), safe="")
    ascii_name = ascii_fallback_filename(artifact)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
