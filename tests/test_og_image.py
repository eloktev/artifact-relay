import io

from PIL import Image

SECRET = "СЕКРЕТНОЕ-ТЕЛО-НЕ-ДОЛЖНО-ПОПАСТЬ-В-КАРТИНКУ"


def test_og_image_is_public_and_is_a_real_png(client, publish):
    artifact_id = publish(title="Отчёт о нагрузке", content=f"# t\n\n{SECRET}\n".encode()).json()[
        "id"
    ]

    response = client.get(f"/a/{artifact_id}/og.png")

    assert response.status_code == 200, "the OG card must not require a session"
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    image = Image.open(io.BytesIO(response.content))
    assert image.format == "PNG"
    assert image.size == (1200, 630)


def test_og_image_carries_no_private_bytes_or_secrets(client, publish, settings):
    artifact_id = publish(content=f"# t\n\n{SECRET}\n".encode()).json()["id"]

    blob = client.get(f"/a/{artifact_id}/og.png").content

    for secret in (
        SECRET.encode(),
        settings.artifact_api_token.get_secret_value().encode(),
        settings.session_secret_key.get_secret_value().encode(),
        settings.view_password_hash.get_secret_value().encode(),
    ):
        assert secret not in blob

    image = Image.open(io.BytesIO(blob))
    assert not image.info.get("exif")
    assert set(image.info) <= {"dpi", "srgb", "gamma", "icc_profile", "transparency"}, image.info


def test_og_cards_differ_per_artifact_and_are_deterministic(client, publish):
    first = publish(title="Первый отчёт").json()["id"]
    second = publish(title="Второй отчёт").json()["id"]

    a1 = client.get(f"/a/{first}/og.png").content
    a2 = client.get(f"/a/{first}/og.png").content
    b1 = client.get(f"/a/{second}/og.png").content

    assert a1 == a2, "the same artifact must produce a byte-identical card"
    assert a1 != b1, "different titles must produce different cards"


def test_og_card_renders_cyrillic_titles_without_blanks():
    from artifact_relay.ogimage import render_card

    blank = Image.open(io.BytesIO(render_card(title="", kind="markdown", created="1 мая 2026")))
    filled = Image.open(
        io.BytesIO(
            render_card(
                title="Очень длинный заголовок с кириллицей и переносом строки",
                kind="markdown",
                created="1 мая 2026",
            )
        )
    )

    assert blank.tobytes() != filled.tobytes(), "the title is not being drawn"


def test_og_image_of_unknown_artifact_is_404(client):
    assert client.get("/a/nope/og.png").status_code == 404


def test_html_and_markdown_cards_are_labelled_differently(client, publish):
    md = publish(title="Одинаковый заголовок", fmt="markdown").json()["id"]
    html = publish(title="Одинаковый заголовок", fmt="html", content=b"<p>x</p>").json()["id"]

    assert client.get(f"/a/{md}/og.png").content != client.get(f"/a/{html}/og.png").content
