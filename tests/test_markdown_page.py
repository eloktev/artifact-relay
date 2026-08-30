import re

DOC = """# Отчёт о миграции

Вводный абзац со **важным** словом.

## Результаты

| Сервис | Статус | RPS |
| --- | ---: | ---: |
| api | ok | 1200 |
| worker | degraded | 40 |

## Что дальше

- [x] Выкатить схему
- [ ] Переключить трафик

### Пример кода

```python
def total(rows: list[int]) -> int:
    return sum(rows)
```

## Диаграмма

```mermaid
graph TD
  A[Клиент] --> B[API]
```
"""


def test_markdown_page_renders_document_structure(publish, logged_in):
    artifact_id = publish(content=DOC.encode()).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert "<h1" in html and "Отчёт о миграции" in html
    assert "<table" in html and "<thead" in html and "<td" in html
    assert 'type="checkbox"' in html and "disabled" in html
    assert re.search(r'<span class="k">def</span>', html), "python code is not highlighted"
    assert "<pre" in html


def test_markdown_page_has_a_table_of_contents_with_working_anchors(publish, logged_in):
    artifact_id = publish(content=DOC.encode()).json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    from artifact_relay.rendering import render_markdown

    toc = render_markdown(DOC).toc
    assert [entry.text for entry in toc] == [
        "Отчёт о миграции",
        "Результаты",
        "Что дальше",
        "Пример кода",
        "Диаграмма",
    ]

    for entry in toc:
        assert f'id="{entry.anchor}"' in html, f"missing anchor target {entry.anchor}"
        assert f'href="#{entry.anchor}"' in html, f"missing TOC link to {entry.anchor}"


def test_anchors_are_stable_and_unique():
    from artifact_relay.rendering import render_markdown

    source = "# Итоги\n\n## Раздел\n\n## Раздел\n"
    first = render_markdown(source).toc
    second = render_markdown(source).toc

    assert [e.anchor for e in first] == [e.anchor for e in second], "anchors must be stable"
    assert len({e.anchor for e in first}) == 3, "duplicate headings must get distinct anchors"


def test_mermaid_uses_a_locally_bundled_asset_not_a_cdn(publish, logged_in):
    artifact_id = publish(content=DOC.encode()).json()["id"]

    page = logged_in.get(f"/a/{artifact_id}")
    html = page.text

    assert '<div class="mermaid">' in html
    assert "graph TD" in html

    sources = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    assert sources, "no script tags on a page containing a Mermaid diagram"
    assert any("mermaid" in src for src in sources), sources
    for src in sources:
        assert src.startswith("/static/"), f"remote script reference: {src}"
    assert "cdn.jsdelivr.net" not in html and "unpkg.com" not in html


def test_pages_without_mermaid_do_not_load_the_bundle(publish, logged_in):
    artifact_id = publish(content=b"# Just text\n\nNo diagrams here.\n").json()["id"]

    html = logged_in.get(f"/a/{artifact_id}").text

    assert "mermaid" not in html.lower()


def test_tables_are_wrapped_so_they_can_scroll_on_a_phone():
    from artifact_relay.rendering import render_markdown

    html = render_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n").html

    assert '<div class="doc__table">' in html
    assert html.index('<div class="doc__table">') < html.index("<table>")
    assert html.rstrip().endswith("</div>")


def test_task_list_items_are_marked_up_for_styling():
    from artifact_relay.rendering import render_markdown

    html = render_markdown("- [x] готово\n- [ ] нет\n").html

    assert "task-list-item" in html
    assert html.count('type="checkbox"') == 2
    assert html.count("disabled") == 2
