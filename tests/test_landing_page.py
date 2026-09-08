from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from defusedxml import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
INDEX = SITE / "index.html"
SELF_HOST = SITE / "self-host" / "index.html"
SECURE_PUBLISHING = SITE / "guides" / "secure-agent-publishing" / "index.html"
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


def parsed_page(path: Path) -> tuple[str, LandingParser]:
    source = path.read_text(encoding="utf-8")
    parser = LandingParser()
    parser.feed(source)
    return source, parser


def structured_data(source: str) -> dict[str, object]:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', source, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    assert isinstance(data, dict)
    return data


def test_landing_has_self_contained_distribution() -> None:
    assert INDEX.is_file()
    assert (SITE / "styles.css").is_file()
    assert (SITE / "script.js").is_file()
    assert (SITE / "assets" / "relay-flow.svg").is_file()
    assert (SITE / "assets" / "artifact-library.webp").is_file()
    assert (SITE / "assets" / "publish-private-view.webp").is_file()

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
    for resource in resource_urls:
        url = urlsplit(resource)
        assert not url.scheme and not url.netloc
        resolved = (SITE / url.path).resolve()
        assert resolved.is_relative_to(SITE.resolve())
        assert resolved.is_file(), resource


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
        "Connect with Hermes",
        "Don’t deploy Artifact Relay. Ask your agent to connect it.",
        "Managed beta — free during beta",
        "Planned price: $24/year",
        "Limited availability",
        "hosted and maintained for you",
        "Why not",
        "MIT",
        "v1.2.0",
        "Setup result",
    )
    for phrase in required_copy:
        assert phrase.casefold() in text.casefold(), phrase

    assert "revolutionary" not in source.casefold()
    assert "secure by design" not in source.casefold()
    assert "any vps" not in source.casefold()
    assert "your own vps" not in source.casefold()

    links = [attrs for tag, attrs in parser.tags if tag == "a"]
    hrefs = {attrs.get("href") for attrs in links}
    assert "https://github.com/eloktev/artifact-relay" in hrefs
    assert "https://github.com/eloktev/hermes-artifact-relay" in hrefs
    assert "https://github.com/eloktev/artifact-relay/releases/tag/v1.2.0" in hrefs
    assert "https://relay.lok-labs.com/" in hrefs
    assert "hermes://plugin/install?repo=eloktev/hermes-artifact-relay&enable=1" in hrefs

    primary_links = [
        attrs.get("href")
        for tag, attrs in parser.tags
        if tag == "a" and "button-primary" in attrs.get("class", "")
    ]
    assert primary_links[0] == "https://relay.lok-labs.com/"
    assert "/self-host/" in hrefs
    assert "/guides/secure-agent-publishing/" in hrefs


def test_self_host_page_has_unique_metadata_and_howto_schema() -> None:
    source, parser = parsed_page(SELF_HOST)
    home_source, _ = parsed_landing()
    titles = re.findall(r"<title>(.*?)</title>", source, re.DOTALL)
    home_titles = re.findall(r"<title>(.*?)</title>", home_source, re.DOTALL)
    h1 = re.findall(r"<h1>(.*?)</h1>", source, re.DOTALL)
    home_h1 = re.findall(r"<h1>(.*?)</h1>", home_source, re.DOTALL)

    assert SELF_HOST.is_file()
    assert len(titles) == 1
    assert titles != home_titles
    assert len([tag for tag, _ in parser.tags if tag == "h1"]) == 1
    assert h1 == ["Self-host Artifact Relay. Publish something useful."]
    assert h1 != home_h1
    assert '<link rel="canonical" href="https://artifact-relay.lok-labs.com/self-host/">' in source
    assert (
        '<meta property="og:url" content="https://artifact-relay.lok-labs.com/self-host/">'
        in source
    )
    assert '<meta property="og:type" content="website">' in source
    assert '<meta property="og:title"' in source
    assert '<meta property="og:description"' in source
    assert '<meta name="twitter:card" content="summary_large_image">' in source
    assert '<meta name="twitter:title"' in source
    assert '<meta name="twitter:description"' in source

    data = structured_data(source)
    assert data["@type"] == "HowTo"
    assert data["url"] == "https://artifact-relay.lok-labs.com/self-host/"
    assert data["totalTime"] == "PT10M"
    steps = data["step"]
    assert isinstance(steps, list)
    assert [step["position"] for step in steps] == list(range(1, len(steps) + 1))
    assert len(steps) >= 7


def test_self_host_page_is_accessible_and_uses_internal_shared_assets() -> None:
    source, parser = parsed_page(SELF_HOST)
    tags = [tag for tag, _ in parser.tags]
    assert '<html lang="en">' in source
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in source
    assert tags.count("h1") == 1
    assert {"header", "nav", "main", "footer"} <= set(tags)
    assert 'href="#main"' in source
    assert 'class="skip-link"' in source
    assert 'aria-label="Primary navigation"' in source
    assert 'id="copy-status"' in source
    assert 'role="status"' in source
    assert 'aria-live="polite"' in source

    resources = [
        attrs[key]
        for tag, attrs in parser.tags
        for key in ("src", "href")
        if key in attrs
        and (
            tag in {"img", "script", "source"}
            or (tag == "link" and attrs.get("rel") == "stylesheet")
        )
    ]
    assert resources
    for resource in resources:
        url = urlsplit(resource)
        assert not url.scheme and not url.netloc
        resolved = (SELF_HOST.parent / url.path).resolve()
        assert resolved.is_relative_to(SITE.resolve())
        assert resolved.is_file(), resource

    asset_versions = {
        urlsplit(resource).query
        for resource in resources
        if urlsplit(resource).path.endswith((".css", ".js"))
    }
    assert len(asset_versions) == 1
    assert "" not in asset_versions


def test_self_host_page_documents_first_value_without_exposing_secrets() -> None:
    source, parser = parsed_page(SELF_HOST)
    text = " ".join(" ".join(parser.text_parts).split())
    required = (
        "Docker Engine",
        "Compose v2",
        "OpenSSL",
        "POSIX shell",
        "git clone https://github.com/eloktev/artifact-relay.git",
        "git checkout v1.2.0",
        "docker build -t artifact-relay:1.2.0 .",
        "./scripts/bootstrap.sh",
        "docker compose up -d",
        "curl -fsS http://localhost:8000/api/health",
        "Authorization: Bearer ${ARTIFACT_API_TOKEN}",
        "POST http://localhost:8000/api/artifacts",
        'json.load(sys.stdin)["url"]',
        'open "$ARTIFACT_URL"',
        "viewer password",
        "API token",
        "docker compose down",
    )
    for phrase in required:
        assert phrase in text, phrase

    hrefs = {attrs.get("href") for tag, attrs in parser.tags if tag == "a"}
    assert "https://github.com/eloktev/artifact-relay#localhost-quick-start" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/docs/VPS.md" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/SECURITY.md" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/docs/BACKUP_RESTORE.md" in hrefs
    assert "../guides/secure-agent-publishing/" in hrefs

    assert "replace-me-with-at-least" not in source
    assert "$argon2id$" not in source
    assert not re.search(r"ARTIFACT_API_TOKEN\s*=\s*['\"]?[A-Za-z0-9+/]{16,}", source)
    assert "cat .env" not in source


def test_secure_publishing_article_has_canonical_metadata_and_schema() -> None:
    source, parser = parsed_page(SECURE_PUBLISHING)
    titles = re.findall(r"<title>(.*?)</title>", source, re.DOTALL)

    assert SECURE_PUBLISHING.is_file()
    assert titles == [
        "Credential boundaries for secure AI-agent artifact publishing — Artifact Relay"
    ]
    assert len([tag for tag, _ in parser.tags if tag == "h1"]) == 1
    assert (
        '<link rel="canonical" href="https://artifact-relay.lok-labs.com/guides/secure-agent-publishing/">'
        in source
    )
    assert (
        '<meta property="og:url" content="https://artifact-relay.lok-labs.com/guides/secure-agent-publishing/">'
        in source
    )
    assert '<meta property="og:type" content="article">' in source
    assert '<meta name="description"' in source
    assert '<meta property="og:title"' in source
    assert '<meta property="og:description"' in source
    assert '<meta name="twitter:card" content="summary_large_image">' in source

    data = structured_data(source)
    assert data["@type"] == "TechArticle"
    assert data["headline"] == "Credential boundaries for secure AI-agent artifact publishing"
    assert data["url"] == ("https://artifact-relay.lok-labs.com/guides/secure-agent-publishing/")
    assert data["isPartOf"] == {"@id": "https://artifact-relay.lok-labs.com/"}
    assert data["author"] == {"@type": "Organization", "name": "Artifact Relay"}


def test_secure_publishing_article_covers_the_operational_security_model() -> None:
    source, parser = parsed_page(SECURE_PUBLISHING)
    text = " ".join(" ".join(parser.text_parts).split())
    required = (
        "Publisher credential",
        "Viewer credential",
        "Artifact-scoped share credential",
        "Threat model and trust boundaries",
        "Bearer token",
        "provenance-metadata update",
        "viewer password",
        "one rendered artifact",
        "Treat share URLs as credentials",
        "127.0.0.1",
        "SHARE_LINKS_ENABLED=false",
        "docker compose up -d",
        "./scripts/backup.sh",
        "./scripts/restore.sh",
        "artifact-relay-data.tar.gz",
        "Secrets are not in the data archive",
        "Operational checklist",
        "Non-goals",
        "single-user",
    )
    for phrase in required:
        assert phrase.casefold() in text.casefold(), phrase

    tables = [attrs for tag, attrs in parser.tags if tag == "table"]
    assert tables
    assert any(attrs.get("aria-label") == "Artifact Relay trust boundaries" for attrs in tables)
    assert text.count("Publisher credential") >= 2
    assert text.count("Viewer credential") >= 2
    assert text.count("Artifact-scoped share credential") >= 2

    hrefs = {attrs.get("href") for tag, attrs in parser.tags if tag == "a"}
    assert "../../" in hrefs
    assert "../../self-host/" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/README.md#api" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/SECURITY.md" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/docs/BACKUP_RESTORE.md" in hrefs
    assert "https://github.com/eloktev/artifact-relay/blob/main/docs/VPS.md" in hrefs

    assert "replace-me-with-at-least" not in source
    assert "$argon2id$" not in source
    assert not re.search(r"ARTIFACT_API_TOKEN\s*=\s*['\"]?[A-Za-z0-9+/]{16,}", source)
    assert "cat .env" not in source


def test_secure_publishing_article_uses_nested_shared_assets() -> None:
    source, parser = parsed_page(SECURE_PUBLISHING)
    assert '<html lang="en">' in source
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in source
    tags = [tag for tag, _ in parser.tags]
    assert {"header", "nav", "main", "article", "footer"} <= set(tags)
    assert 'href="#main"' in source
    assert 'class="skip-link"' in source
    assert 'aria-label="Primary navigation"' in source

    resources = [
        attrs[key]
        for tag, attrs in parser.tags
        for key in ("src", "href")
        if key in attrs
        and (
            tag in {"img", "script", "source"}
            or (tag == "link" and attrs.get("rel") == "stylesheet")
        )
    ]
    assert resources
    for resource in resources:
        url = urlsplit(resource)
        assert not url.scheme and not url.netloc
        resolved = (SECURE_PUBLISHING.parent / url.path).resolve()
        assert resolved.is_relative_to(SITE.resolve())
        assert resolved.is_file(), resource

    asset_versions = {
        urlsplit(resource).query
        for resource in resources
        if urlsplit(resource).path.endswith((".css", ".js"))
    }
    assert asset_versions == {"v=20260906-self-host"}


def test_landing_has_accessible_semantic_shell() -> None:
    source, parser = parsed_landing()
    tags = [tag for tag, _ in parser.tags]

    assert '<html lang="en">' in source
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in source
    assert '<meta name="description"' in source
    assert '<meta property="og:title"' in source
    assert '<meta property="og:description"' in source
    assert '<meta property="og:type" content="website">' in source
    assert '<meta property="og:url" content="https://artifact-relay.lok-labs.com/">' in source
    assert (
        '<meta property="og:image" '
        'content="https://artifact-relay.lok-labs.com/assets/artifact-library.webp">' in source
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in source
    assert '<link rel="canonical" href="https://artifact-relay.lok-labs.com/">' in source
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


def test_landing_exposes_search_and_agent_discovery_metadata() -> None:
    source = INDEX.read_text(encoding="utf-8")
    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    sitemap = ET.parse(SITE / "sitemap.xml")
    llms = (SITE / "llms.txt").read_text(encoding="utf-8")

    data = structured_data(source)
    assert data["@type"] == "SoftwareApplication"
    assert data["url"] == "https://artifact-relay.lok-labs.com/"
    assert data["codeRepository"] == "https://github.com/eloktev/artifact-relay"
    assert data["license"] == "https://opensource.org/license/mit"

    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert "Sitemap: https://artifact-relay.lok-labs.com/sitemap.xml" in robots

    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [node.text for node in sitemap.findall(".//s:loc", namespace)]
    assert locations == [
        "https://artifact-relay.lok-labs.com/",
        "https://artifact-relay.lok-labs.com/self-host/",
        "https://artifact-relay.lok-labs.com/guides/secure-agent-publishing/",
    ]

    assert "# Artifact Relay" in llms
    assert "https://github.com/eloktev/artifact-relay" in llms
    assert "https://github.com/eloktev/hermes-artifact-relay" in llms
    assert "https://relay.lok-labs.com/" in llms
    assert "https://artifact-relay.lok-labs.com/self-host/" in llms
    assert "https://artifact-relay.lok-labs.com/guides/secure-agent-publishing/" in llms

    indexnow_files = [
        path for path in SITE.glob("*.txt") if re.fullmatch(r"[0-9a-f]{32}\.txt", path.name)
    ]
    assert len(indexnow_files) == 1
    indexnow_key = indexnow_files[0].stem
    assert indexnow_files[0].read_text(encoding="utf-8").strip() == indexnow_key


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


def test_readme_and_landing_show_the_real_first_value_flow() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    source, parser = parsed_landing()
    text = " ".join(" ".join(parser.text_parts).split())
    demo_path = "site/assets/publish-private-view.webp"

    assert demo_path in readme
    assert "Publish → private view" in readme
    assert "captured from a local v1.2.0 run" in readme
    assert "Publish → private view" in text
    assert "captured from a local v1.2.0 run" in text

    images = [attrs for tag, attrs in parser.tags if tag == "img"]
    assert any(
        attrs.get("src") == "assets/publish-private-view.webp"
        and attrs.get("alt")
        == (
            "A real local Artifact Relay run: an API publish succeeds, then the "
            "private viewer renders the Markdown artifact"
        )
        for attrs in images
    )
    assert source.count("assets/publish-private-view.webp") == 1
