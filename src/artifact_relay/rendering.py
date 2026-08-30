"""Markdown -> sanitised HTML.

Pipeline: markdown-it renders to HTML, then **every** byte of that HTML goes through nh3
(Rust `ammonia`) with an explicit allowlist. Sanitising the rendered output rather than the
Markdown source means raw HTML embedded in the source is covered by the same allowlist, and
nothing that markdown-it itself can emit is trusted implicitly either.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import urlsplit

import nh3
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from artifact_relay.assets import is_safe_asset_name

MERMAID_LANGUAGES = {"mermaid"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
ANCHOR_PREFIX = "h-"
TOC_MAX_LEVEL = 4

_SAFE_ANCHOR = re.compile(rf"{ANCHOR_PREFIX}[\w-]{{1,120}}", re.UNICODE)
_STRIP_PUNCTUATION = re.compile(r"[^\w\s-]", re.UNICODE)
_COLLAPSE = re.compile(r"[\s_]+", re.UNICODE)

ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "s",
    "del",
    "ins",
    "mark",
    "small",
    "sub",
    "sup",
    "blockquote",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "colgroup",
    "col",
    "pre",
    "code",
    "kbd",
    "samp",
    "var",
    "abbr",
    "span",
    "div",
    "section",
    "a",
    "img",
    "figure",
    "figcaption",
    "details",
    "summary",
    "input",
}

# `class` is permitted only where we ourselves emit it (Pygments spans, Mermaid holders,
# task-list items). A class name cannot execute script; the stylesheet is ours and fixed.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "th": {"colspan", "rowspan", "scope", "align"},
    "td": {"colspan", "rowspan", "align"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start"},
    "abbr": {"title"},
    "details": {"open"},
    "input": {"checked", "disabled"},
    "span": {"class"},
    "code": {"class"},
    "pre": {"class"},
    "div": {"class"},
    "li": {"class"},
    "ul": {"class"},
    "table": {"class"},
    "section": {"class"},
    "h1": {"id"},
    "h2": {"id"},
    "h3": {"id"},
    "h4": {"id"},
    "h5": {"id"},
    "h6": {"id"},
}

# Only `data:image/...` is tolerated, and only as an <img src>; see `_attribute_filter`.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "data"}

_FORMATTER = HtmlFormatter(cssclass="hl", wrapcode=True, nowrap=False)


@dataclass(frozen=True, slots=True)
class TocEntry:
    level: int
    text: str
    anchor: str


@dataclass(slots=True)
class RenderedMarkdown:
    html: str
    toc: list[TocEntry] = field(default_factory=list)
    has_mermaid: bool = False


def slugify(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).strip().lower()
    value = _STRIP_PUNCTUATION.sub("", value)
    value = _COLLAPSE.sub("-", value).strip("-")
    return value or "section"


# Where an artifact-relative reference is meaningful. Everything else keeps its value.
ASSET_REFERENCE_ATTRIBUTES = {("img", "src"), ("a", "href")}
ASSETS_PREFIX = "assets/"


def resolve_asset_reference(value: str, asset_base: str) -> str | None:
    """Map an artifact-relative reference onto the published asset URL.

    The rendered page lives at ``/a/<id>``, so the browser would resolve ``chart.png`` to
    ``/a/chart.png`` and ``assets/chart.png`` to ``/a/assets/chart.png``. Both spellings are
    how people actually write Markdown next to their attachments, so both are accepted and
    both land on ``/a/<id>/assets/<name>``.

    Returns ``None`` — meaning "not mine, leave it exactly as it was" — for anything that is
    not a *flat, safe* asset name: absolute URLs, site-absolute paths, in-page anchors,
    ``data:`` URLs, traversal, and nested paths. The name is checked against the same
    allowlist that publishing and serving use, so no third spelling of "safe" appears here.
    """
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return None

    path = parts.path.removeprefix("./")
    candidate = path.removeprefix(ASSETS_PREFIX) if path.startswith(ASSETS_PREFIX) else path
    if not is_safe_asset_name(candidate):
        return None

    suffix = f"?{parts.query}" if parts.query else ""
    suffix += f"#{parts.fragment}" if parts.fragment else ""
    return f"{asset_base}{candidate}{suffix}"


def _build_attribute_filter(asset_base: str | None) -> Callable[[str, str, str], str | None]:
    def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
        if attribute in {"href", "src"}:
            lowered = value.strip().lower()
            if lowered.startswith("data:"):
                # Inline images only. `data:text/html` in an <a href> is a same-origin XSS.
                if tag == "img" and attribute == "src" and lowered.startswith("data:image/"):
                    return value
                return None
            if asset_base is not None and (tag, attribute) in ASSET_REFERENCE_ATTRIBUTES:
                resolved = resolve_asset_reference(value, asset_base)
                if resolved is not None:
                    return resolved
            return value
        if attribute == "id":
            if tag in HEADING_TAGS and _SAFE_ANCHOR.fullmatch(value):
                return value
            return None
        if attribute == "target":
            return "_blank" if value == "_blank" else None
        return value

    return _attribute_filter


def sanitize_html(html: str, asset_base: str | None = None) -> str:
    """Reduce arbitrary HTML to the allowlist above.

    ``asset_base`` (e.g. ``/a/<id>/assets/``) additionally rewrites artifact-relative asset
    references. The rewrite lives *inside* the sanitiser's own attribute filter, which nh3
    invokes only for attributes that already survived tag, attribute and URL-scheme
    filtering — so `javascript:` never reaches it and embedded raw HTML cannot bypass it.
    """
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        clean_content_tags={"script", "style", "title", "textarea", "noscript", "iframe"},
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_build_attribute_filter(asset_base),
        url_schemes=ALLOWED_URL_SCHEMES,
        url_relative="pass_through",
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
        tag_attribute_values={"input": {"type": {"checkbox"}}},
        set_tag_attribute_values={"input": {"disabled": ""}},
    )


def _render_fence(
    self: Any,
    tokens: list[Token],
    index: int,
    options: Mapping[str, Any],
    env: MutableMapping[str, Any],
) -> str:
    token = tokens[index]
    info = (token.info or "").strip()
    language = info.split()[0].lower() if info else ""

    if language in MERMAID_LANGUAGES:
        env["has_mermaid"] = True
        # The diagram source is escaped; Mermaid reads it as text and draws client-side.
        return f'<div class="mermaid">{escape(token.content)}</div>\n'

    if language:
        try:
            lexer = get_lexer_by_name(language, stripall=False)
        except ClassNotFound:
            lexer = None
        if lexer is not None:
            highlighted: str = highlight(token.content, lexer, _FORMATTER)
            return highlighted

    return f'<div class="hl"><pre><code>{escape(token.content)}</code></pre></div>\n'


def _render_table_open(
    self: Any,
    tokens: list[Token],
    index: int,
    options: Mapping[str, Any],
    env: MutableMapping[str, Any],
) -> str:
    """Wrap tables so a wide table scrolls inside its own box on a phone."""
    return '<div class="doc__table"><table>\n'


def _render_table_close(
    self: Any,
    tokens: list[Token],
    index: int,
    options: Mapping[str, Any],
    env: MutableMapping[str, Any],
) -> str:
    return "</table></div>\n"


def _build_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False})
    parser.enable(["table", "strikethrough"])
    parser.use(tasklists_plugin, enabled=True, label=False)
    parser.add_render_rule("fence", _render_fence)
    parser.add_render_rule("table_open", _render_table_open)
    parser.add_render_rule("table_close", _render_table_close)
    return parser


_PARSER = _build_parser()


def _assign_anchors(tokens: list[Token]) -> list[TocEntry]:
    toc: list[TocEntry] = []
    used: dict[str, int] = {}
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        level = int(token.tag[1])
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        text = inline.content.strip() if inline is not None else ""
        base = f"{ANCHOR_PREFIX}{slugify(text)}"[:120]
        seen = used.get(base, 0)
        used[base] = seen + 1
        anchor = base if seen == 0 else f"{base}-{seen}"
        token.attrSet("id", anchor)
        if level <= TOC_MAX_LEVEL:
            toc.append(TocEntry(level=level, text=text, anchor=anchor))
    return toc


def render_markdown(source: str, asset_base: str | None = None) -> RenderedMarkdown:
    env: dict[str, Any] = {"has_mermaid": False}
    tokens = _PARSER.parse(source, env)
    toc = _assign_anchors(tokens)
    raw_html = _PARSER.renderer.render(tokens, _PARSER.options, env)
    clean = sanitize_html(raw_html, asset_base=asset_base)
    surviving = {entry.anchor for entry in toc if f'id="{entry.anchor}"' in clean}
    return RenderedMarkdown(
        html=clean,
        toc=[entry for entry in toc if entry.anchor in surviving],
        has_mermaid=bool(env.get("has_mermaid")) and 'class="mermaid"' in clean,
    )


def pygments_stylesheet(style: str) -> str:
    # types-Pygments leaves get_style_defs unannotated; the return really is a str.
    defs: str = HtmlFormatter(style=style, cssclass="hl").get_style_defs(  # type: ignore[no-untyped-call]
        ".hl"
    )
    return defs
