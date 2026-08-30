"""Regression: an artifact-relative asset reference in Markdown must resolve.

The rendered page lives at `/a/<id>`, so a browser resolves `chart.png` to `/a/chart.png` and
`assets/chart.png` to `/a/assets/chart.png` — neither of which exists. Every published
attachment was therefore unreachable from the document that referenced it, which is the
normal way anyone writes Markdown with an image.

Both spellings must land on the artifact's own `/a/<id>/assets/<name>`, and nothing else may
be touched: external links, `data:` images, in-page anchors and site-absolute paths keep
working, and sanitisation is unchanged.
"""

from __future__ import annotations

import re

import pytest

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)
CSV = b"a,b\n1,2\n"


def article(html: str) -> str:
    """Just the rendered document, without the page chrome (nav, TOC, colophon)."""
    match = re.search(r'<article class="doc">(.*?)</article>', html, re.DOTALL)
    assert match, "the viewer page has no rendered article"
    return match.group(1)


def sources(html: str) -> list[str]:
    return re.findall(r'<img[^>]*\ssrc="([^"]*)"', article(html))


def hrefs(html: str) -> list[str]:
    return re.findall(r'<a[^>]*\shref="([^"]*)"', article(html))


@pytest.mark.parametrize(
    "reference",
    ["chart.png", "assets/chart.png", "./chart.png", "./assets/chart.png"],
)
def test_every_artifact_relative_spelling_resolves_to_the_published_asset(
    publish, logged_in, reference
):
    artifact_id = publish(
        content=f"# Отчёт\n\n![Диаграмма]({reference})\n".encode(),
        assets=[("chart.png", PNG)],
    ).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert sources(html) == [f"/a/{artifact_id}/assets/chart.png"], html


def test_the_rewritten_reference_actually_serves_the_bytes(publish, logged_in):
    """End to end: follow the URL the page emits and get the published file back."""
    artifact_id = publish(
        content=b"# Report\n\n![c](chart.png)\n", assets=[("chart.png", PNG)]
    ).json()["id"]

    src = sources(logged_in.get(f"/a/{artifact_id}").text)[0]
    response = logged_in.get(src)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG


def test_a_link_to_an_attachment_is_resolved_too(publish, logged_in):
    artifact_id = publish(
        content=b"# Report\n\n[data](assets/data.csv) and [raw](data.csv)\n",
        assets=[("data.csv", CSV)],
    ).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert hrefs(html) == [
        f"/a/{artifact_id}/assets/data.csv",
        f"/a/{artifact_id}/assets/data.csv",
    ]
    assert logged_in.get(f"/a/{artifact_id}/assets/data.csv").content == CSV


def test_raw_html_in_the_markdown_source_is_resolved_by_the_same_rule(publish, logged_in):
    """The rewrite hangs off the sanitiser, so embedded HTML cannot bypass it."""
    artifact_id = publish(
        content=b'# R\n\n<img src="assets/chart.png" alt="c">\n',
        assets=[("chart.png", PNG)],
    ).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert sources(html) == [f"/a/{artifact_id}/assets/chart.png"]


EXTERNAL = """# Заголовок

![remote](https://cdn.example.com/x.png)
![inline](data:image/png;base64,iVBORw0KGgo=)

[anchor](#h-zagolovok)
[absolute](/static/css/app.css)
[mail](mailto:someone@example.com)
[external](https://example.com/page)
"""


def test_external_data_anchor_and_absolute_urls_are_left_alone(publish, logged_in):
    artifact_id = publish(content=EXTERNAL.encode(), assets=[("chart.png", PNG)]).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert sources(html) == [
        "https://cdn.example.com/x.png",
        "data:image/png;base64,iVBORw0KGgo=",
    ]
    assert hrefs(html) == [
        "#h-zagolovok",
        "/static/css/app.css",
        "mailto:someone@example.com",
        "https://example.com/page",
    ]
    assert f"/a/{artifact_id}/assets/" not in html


HOSTILE = """# H

![up](../../etc/passwd)
![deep](assets/nested/chart.png)
![dot](.hidden.png)
[js](javascript:alert(1))
<img src="chart.png" onerror="alert(1)">
"""


def test_hostile_or_unresolvable_references_are_not_rewritten_and_stay_sanitised(
    publish, logged_in
):
    artifact_id = publish(content=HOSTILE.encode(), assets=[("chart.png", PNG)]).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    # Nothing that is not a flat, safe asset name may be pointed at the assets directory.
    assert f"/a/{artifact_id}/assets/passwd" not in html
    assert f"/a/{artifact_id}/assets/nested" not in html
    assert "onerror" not in html
    # markdown-it refuses to build the link at all, so `javascript:` survives only as escaped
    # text. What must never exist is an attribute carrying it.
    assert 'href="javascript:' not in html
    assert 'src="javascript:' not in html
    assert "javascript:" not in " ".join(sources(html) + hrefs(html))
    for rewritten in sources(html):
        if rewritten.startswith(f"/a/{artifact_id}/assets/"):
            assert rewritten == f"/a/{artifact_id}/assets/chart.png"


def test_a_query_string_or_fragment_survives_the_rewrite(publish, logged_in):
    artifact_id = publish(
        content=b"# R\n\n![c](chart.png?v=2)\n\n![d](assets/chart.png#top)\n",
        assets=[("chart.png", PNG)],
    ).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert sources(html) == [
        f"/a/{artifact_id}/assets/chart.png?v=2",
        f"/a/{artifact_id}/assets/chart.png#top",
    ]


def test_rendering_without_an_asset_base_leaves_relative_references_untouched():
    """The renderer is reusable outside a request; the rewrite is opt-in."""
    from artifact_relay.rendering import render_markdown

    assert 'src="chart.png"' in render_markdown("![c](chart.png)").html
    assert (
        'src="/a/X/assets/chart.png"'
        in render_markdown("![c](chart.png)", asset_base="/a/X/assets/").html
    )
