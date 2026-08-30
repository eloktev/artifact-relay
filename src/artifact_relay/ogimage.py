"""Branded Open Graph card for Telegram link previews.

The card is what a link preview shows *before* anyone logs in, so it may contain only what
is already public in the page's meta tags: the title, the artifact kind and the publication
date. Never the body, never a summary of the body, never anything derived from it.

Rendering is deterministic — same inputs, byte-identical PNG — so the card can be cached
hard and compared in tests. No EXIF, no text chunks, no timestamps are written.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
MARGIN = 84
FONT_DIR = Path(__file__).parent / "static" / "fonts"
REGULAR = FONT_DIR / "DejaVuSans.ttf"
BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"

BACKGROUND = (252, 251, 249)
INK = (26, 26, 24)
MUTED = (110, 106, 99)
RULE = (222, 218, 210)

TITLE_SIZE = 64
TITLE_LEADING = 84
MAX_TITLE_LINES = 4

KIND_LABELS = {"markdown": "ДОКУМЕНТ", "html": "ИНТЕРАКТИВНЫЙ МАТЕРИАЛ"}


@lru_cache(maxsize=8)
def _font(path_name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / path_name), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getlength(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines:
        consumed = len(" ".join(lines).split())
        if consumed < len(words):
            last = lines[-1]
            while last and font.getlength(last + " …") > max_width:
                last = last[:-1]
            lines[-1] = f"{last.rstrip()} …"
    return lines


@lru_cache(maxsize=256)
def render_card(*, title: str, kind: str, created: str) -> bytes:
    """Render the card. Cached because the inputs are immutable for an artifact's lifetime."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    eyebrow_font = _font(BOLD.name, 24)
    title_font = _font(BOLD.name, TITLE_SIZE)
    meta_font = _font(REGULAR.name, 28)

    label = KIND_LABELS.get(kind, kind.upper())
    draw.text((MARGIN, MARGIN), label, font=eyebrow_font, fill=MUTED)

    top = MARGIN + 96
    lines = _wrap(title.strip(), title_font, WIDTH - 2 * MARGIN, MAX_TITLE_LINES)
    for index, line in enumerate(lines):
        draw.text((MARGIN, top + index * TITLE_LEADING), line, font=title_font, fill=INK)

    rule_y = HEIGHT - MARGIN - 64
    draw.line([(MARGIN, rule_y), (WIDTH - MARGIN, rule_y)], fill=RULE, width=2)
    draw.text((MARGIN, rule_y + 22), created, font=meta_font, fill=MUTED)

    buffer = io.BytesIO()
    # No pnginfo / exif is passed, so nothing but pixels ends up in the file.
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
