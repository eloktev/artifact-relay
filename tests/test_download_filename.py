"""Regression: the `Content-Disposition` ASCII fallback must be a usable filename.

RFC 6266 wants a plain `filename=` for clients that do not understand `filename*=`. Building
it by replacing every non-ASCII run with `-` turns a fully Cyrillic title — the normal case
for this service — into `-.md`, which strips down to the literal `.md`: a dotfile, hidden on
every Unix client and rejected by some Windows ones. The fallback has to be a *name*.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from artifact_relay.download import content_disposition
from artifact_relay.models import Artifact

ARTIFACT_ID = "Xk7Qw2Lm9Zt4Rb1Nv8Hs3Jd6Fp0Gc5A"


def make(title: str, fmt: str = "markdown") -> Artifact:
    return Artifact(
        id=ARTIFACT_ID,
        title=title,
        summary=None,
        format=fmt,  # type: ignore[arg-type]
        source_filename="source",
        content_bytes=10,
        created_at=datetime.now(UTC),
        expires_at=None,
    )


def ascii_name(header: str) -> str:
    match = re.search(r'filename="([^"]*)"', header)
    assert match, header
    return match.group(1)


@pytest.mark.parametrize(
    "title",
    [
        "Отчёт о нагрузочном тесте",
        "Инфографика",
        "。。。",
        "...",
        "---",
        "___",
        "   ",
        "…",
        "!!!",
    ],
)
def test_a_title_with_no_ascii_letters_falls_back_to_the_artifact_id(title):
    name = ascii_name(content_disposition(make(title)))

    assert name == f"artifact-{ARTIFACT_ID}.md", name
    assert not name.startswith("."), "a dotfile is hidden on every Unix client"
    assert not name.startswith("-"), "a leading dash reads as an option to some tools"
    assert any(character.isalnum() for character in name.removesuffix(".md"))


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Отчёт report", "report.md"),
        ("Отчёт 2024", "2024.md"),
        ("Load test", "Load-test.md"),
        ("v1.2 итог", "v1.2.md"),
        ("report", "report.md"),
    ],
)
def test_the_ascii_part_of_a_mixed_title_is_kept(title, expected):
    assert ascii_name(content_disposition(make(title))) == expected


def test_the_utf8_variant_still_carries_the_real_title():
    from urllib.parse import unquote

    header = content_disposition(make("Отчёт о нагрузочном тесте"))

    match = re.search(r"filename\*=UTF-8''(\S+)", header)
    assert match, header
    assert unquote(match.group(1)) == "Отчёт о нагрузочном тесте.md"


@pytest.mark.parametrize(
    "title",
    [
        'evil"; filename="pwned.md',
        "line\r\nInjected-Header: yes",
        "../../etc/passwd",
        "a" * 500,
        "nul\x00byte",
    ],
)
def test_a_hostile_title_cannot_break_out_of_the_header(title):
    header = content_disposition(make(title))
    name = ascii_name(header)

    assert '"' not in name
    assert "\r" not in header and "\n" not in header
    assert "/" not in name and "\\" not in name
    assert len(name) < 120
    assert header.startswith("attachment; ")


def test_an_html_artifact_keeps_the_html_extension():
    assert ascii_name(content_disposition(make("Инфографика", fmt="html"))).endswith(".html")
    assert ascii_name(content_disposition(make("Dashboard", fmt="html"))) == "Dashboard.html"


def test_the_header_is_served_on_the_real_download_route(publish, logged_in):
    artifact_id = publish(title="Отчёт о нагрузочном тесте").json()["id"]

    header = logged_in.get(f"/a/{artifact_id}/source").headers["content-disposition"]

    assert ascii_name(header) == f"artifact-{artifact_id}.md"
    assert "filename*=UTF-8''" in header
