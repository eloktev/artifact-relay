from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
INDEX = SITE / "index.html"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class LandingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


def parsed_landing() -> tuple[str, LandingParser]:
    source = INDEX.read_text(encoding="utf-8")
    parser = LandingParser()
    parser.feed(source)
    return source, parser


def test_landing_has_self_contained_distribution() -> None:
    assert INDEX.is_file()
    assert (SITE / "styles.css").is_file()
    assert (SITE / "script.js").is_file()
    assert (SITE / "assets" / "relay-flow.svg").is_file()
    assert (SITE / "assets" / "artifact-library.webp").is_file()

    _, parser = parsed_landing()
    resource_urls = [
        attrs[key]
        for tag, attrs in parser.tags
        for key in ("src", "href")
        if key in attrs
        and not attrs[key].startswith("#")
        and (
            tag in {"img", "script", "source"}
            or (tag == "link" and attrs.get("rel") == "stylesheet")
        )
    ]
    assert resource_urls
    assert all(not re.match(r"^https?://", url) for url in resource_urls)
    for url in resource_urls:
        assert (SITE / url).resolve().is_relative_to(SITE.resolve())
        assert (SITE / url).is_file(), url


def test_landing_copy_matches_verified_positioning() -> None:
    source, parser = parsed_landing()
    text = " ".join(" ".join(parser.text_parts).split())

    required_copy = (
        "Private artifact delivery for AI agents",
        "private by default",
        "localhost",
        "a Linux VPS",
        "Markdown",
        "standalone HTML",
        "sharing is disabled by default",
        "Hermes Agent",
        "Why not",
        "MIT",
        "v1.1.0",
        "response excerpt",
    )
    for phrase in required_copy:
        assert phrase.casefold() in text.casefold(), phrase

    assert "revolutionary" not in source.casefold()
    assert "secure by design" not in source.casefold()
    assert "hosted" not in source.casefold()
    assert "pricing" not in source.casefold()
    assert "any vps" not in source.casefold()
    assert "your own vps" not in source.casefold()
    assert '"expires_in_days"' not in source
    assert '"expires_at"' in source

    links = [attrs for tag, attrs in parser.tags if tag == "a"]
    hrefs = {attrs.get("href") for attrs in links}
    assert "https://github.com/eloktev/artifact-relay" in hrefs
    assert "https://github.com/eloktev/hermes-artifact-relay" in hrefs
    assert "https://github.com/eloktev/artifact-relay/releases/tag/v1.1.0" in hrefs


def test_landing_has_accessible_semantic_shell() -> None:
    source, parser = parsed_landing()
    tags = [tag for tag, _ in parser.tags]

    assert '<html lang="en">' in source
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in source
    assert '<meta name="description"' in source
    assert '<meta property="og:title"' in source
    assert '<meta property="og:description"' in source
    assert '<link rel="canonical" href="https://eloktev.github.io/artifact-relay/">' in source
    assert tags.count("h1") == 1
    assert "header" in tags
    assert "nav" in tags
    assert "main" in tags
    assert "footer" in tags
    assert 'href="#main"' in source
    assert 'class="skip-link"' in source
    assert 'aria-label="Primary navigation"' in source
    assert 'role="status"' in source
    assert 'aria-live="polite"' in source

    for tag, attrs in parser.tags:
        if tag == "img":
            assert attrs.get("alt"), attrs
        if tag == "a" and attrs.get("target") == "_blank":
            rel = set(attrs.get("rel", "").split())
            assert {"noopener", "noreferrer"} <= rel, attrs


def test_landing_uses_responsive_and_motion_safe_css() -> None:
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    assert "@media (max-width:" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-x: hidden" not in css
    assert ":focus-visible" in css
    assert "min-height" in css
    assert "clamp(" in css


def test_copy_controls_require_clipboard_and_announce_results() -> None:
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    script = (SITE / "script.js").read_text(encoding="utf-8")
    source = INDEX.read_text(encoding="utf-8")

    assert ".copy {" in css
    assert "display: none" in css
    assert ".clipboard-ready .copy" in css
    assert "navigator.clipboard" in script
    assert 'classList.add("clipboard-ready")' in script
    assert 'getElementById("copy-status")' in script
    assert "status.textContent" in script
    assert 'id="copy-status"' in source
    assert source.count("data-copy-success=") == 3


def test_pages_workflow_deploys_only_static_site() -> None:
    assert PAGES_WORKFLOW.is_file()
    workflow = yaml.safe_load(PAGES_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow[True]["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    jobs = workflow["jobs"]
    assert set(jobs) == {"deploy"}
    deploy = jobs["deploy"]
    assert deploy["environment"]["name"] == "github-pages"
    steps = deploy["steps"]
    uses = [step.get("uses", "") for step in steps]
    assert any(use.startswith("actions/upload-pages-artifact@") for use in uses)
    assert any(use.startswith("actions/deploy-pages@") for use in uses)
    upload = next(
        step for step in steps if step.get("uses", "").startswith("actions/upload-pages-artifact@")
    )
    assert upload["with"]["path"] == "site"
