import re
from html.parser import HTMLParser

HOSTILE = """
# Заголовок

<script>alert('xss')</script>

<img src=x onerror="alert('xss')">

[ссылка](javascript:alert('xss'))

<a href="javascript:alert(1)">js link</a>

<a href="JaVaScRiPt:alert(1)">mixed case js link</a>

<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">data link</a>

<iframe src="https://evil.example/"></iframe>

<svg onload="alert(1)"><circle r="10"/></svg>

<div style="background:url(javascript:alert(1))">styled</div>

<form action="https://evil.example/"><input name="x"></form>

<object data="https://evil.example/"></object>

<embed src="https://evil.example/">

<base href="https://evil.example/">

<link rel="stylesheet" href="https://evil.example/x.css">

<meta http-equiv="refresh" content="0;url=https://evil.example/">

<body onload="alert(1)">

<math><mtext><style><img src=x onerror=alert(1)></style></mtext></math>

<p onmouseover="alert(1)">hover me</p>
"""


def render(markdown: str) -> str:
    from artifact_relay.rendering import render_markdown

    return render_markdown(markdown).html


def article_of(page_html: str) -> str:
    """The artifact body only — the page's own <head> is not the artifact's output."""
    match = re.search(r"<article\b.*?</article>", page_html, re.DOTALL)
    assert match, "artifact page has no <article> body"
    return match.group(0)


FORBIDDEN_TAGS = {
    "script",
    "iframe",
    "object",
    "embed",
    "base",
    "link",
    "meta",
    "form",
    "style",
    "svg",
    "math",
    "textarea",
    "noscript",
    "frame",
    "frameset",
    "applet",
}


class Scanner(HTMLParser):
    """Collect every tag and attribute that survived sanitisation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attributes.append((tag, name.lower(), (value or "")))

    handle_startendtag = handle_starttag


def scan(html: str) -> Scanner:
    scanner = Scanner()
    scanner.feed(html)
    return scanner


def assert_html_is_inert(html: str) -> None:
    scanner = scan(html)

    surviving = FORBIDDEN_TAGS.intersection(scanner.tags)
    assert not surviving, f"dangerous tags survived: {sorted(surviving)}"

    for tag, name, value in scanner.attributes:
        assert not name.startswith("on"), f"event handler survived: <{tag} {name}>"
        assert name != "style", f"style attribute survived on <{tag}>"
        assert name not in {"srcdoc", "formaction", "http-equiv"}, f"<{tag} {name}> survived"
        lowered = value.strip().lower().replace("\t", "").replace("\n", "")
        assert not lowered.startswith("javascript:"), f"<{tag} {name}={value!r}> survived"
        assert not lowered.startswith("vbscript:"), f"<{tag} {name}={value!r}> survived"
        if lowered.startswith("data:"):
            assert tag == "img" and name == "src" and lowered.startswith("data:image/"), (
                f"<{tag} {name}={value!r}> survived"
            )
        assert "evil.example" not in lowered, f"<{tag} {name}={value!r}> survived"


def test_hostile_markdown_is_neutralised():
    assert_html_is_inert(render(HOSTILE))


def test_hostile_markdown_is_neutralised_end_to_end(publish, logged_in):
    artifact_id = publish(content=HOSTILE.encode()).json()["id"]

    page = logged_in.get(f"/a/{artifact_id}")

    assert page.status_code == 200
    assert_html_is_inert(article_of(page.text))
    # The raw source must not be echoed verbatim either.
    assert "<script>alert" not in page.text


def test_benign_markup_survives():
    html = render(
        "# Заголовок\n\n**жирный** и `код`\n\n"
        "[ссылка](https://example.com/x)\n\n"
        "![схема](assets/diagram.png)\n"
    )
    assert "<h1" in html
    assert "<strong>жирный</strong>" in html
    assert "<code>код</code>" in html
    assert 'href="https://example.com/x"' in html
    assert 'src="assets/diagram.png"' in html


def test_links_get_noopener_and_do_not_leak_the_referrer():
    html = render("[внешняя](https://example.com/x)")
    assert "noopener" in html
    assert "noreferrer" in html


def test_inline_data_images_survive_but_only_as_images():
    from artifact_relay.rendering import render_markdown

    tiny = "data:image/png;base64,iVBORw0KGgo="
    html = render_markdown(
        f'<img src="{tiny}" alt="chart">\n\n'
        '<img src="data:text/html;base64,PHNjcmlwdD4=" alt="bad">\n\n'
        f'<a href="{tiny}">link</a>\n'
    ).html

    assert f'src="{tiny}"' in html, "a legitimate inline PNG was stripped"
    assert "data:text/html" not in html
    assert_html_is_inert(html)


def test_target_blank_is_allowed_but_other_targets_are_dropped():
    from artifact_relay.rendering import render_markdown

    html = render_markdown(
        '<a href="https://example.com/a" target="_blank">new tab</a>\n\n'
        '<a href="https://example.com/b" target="victimFrame">reframe</a>\n'
    ).html

    assert 'target="_blank"' in html
    assert "victimFrame" not in html
    # _blank without noopener is a tabnabbing vector; link_rel adds it unconditionally.
    assert "noopener" in html


def test_heading_ids_are_the_only_ids_that_survive():
    from artifact_relay.rendering import render_markdown

    html = render_markdown(
        '# Заголовок\n\n<div id="attributes">clobber</div>\n\n<h2 id="evil onmouseover=x">x</h2>\n'
    ).html

    assert 'id="h-' in html
    assert 'id="attributes"' not in html
    assert "onmouseover" not in html
