import pytest

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def test_assets_are_stored_and_served_with_the_right_media_type(publish, logged_in):
    created = publish(assets=[("chart.png", PNG), ("data.csv", b"a,b\n1,2\n")]).json()
    artifact_id = created["id"]

    assert sorted(created["asset_names"]) == ["chart.png", "data.csv"]

    png = logged_in.get(f"/a/{artifact_id}/assets/chart.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content == PNG

    csv = logged_in.get(f"/a/{artifact_id}/assets/data.csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")


def test_assets_require_a_session(client, publish):
    artifact_id = publish(assets=[("chart.png", PNG)]).json()["id"]

    assert client.get(f"/a/{artifact_id}/assets/chart.png").status_code == 403


def test_active_content_assets_are_never_served_inline(publish, logged_in):
    artifact_id = publish(
        assets=[
            ("page.html", b"<script>alert(1)</script>"),
            ("code.js", b"alert(1)"),
            (
                "vector.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            ),
        ]
    ).json()["id"]

    for name in ("page.html", "code.js"):
        response = logged_in.get(f"/a/{artifact_id}/assets/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment")

    svg = logged_in.get(f"/a/{artifact_id}/assets/vector.svg")
    assert svg.status_code == 200
    # SVG may render in <img>, but a direct navigation must be inert.
    assert "sandbox" in svg.headers["content-security-policy"]
    assert "default-src 'none'" in svg.headers["content-security-policy"]


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "....//....//etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/etc/passwd",
        "..\\..\\windows\\win.ini",
        "%00../etc/passwd",
        "chart.png/../../../../etc/passwd",
        # Percent-encoded: httpx normalises a literal ".." client-side, so the encoded
        # forms are the ones that actually reach the server as a hostile path segment.
        "%2e%2e",
        "%2e",
        "%2e%2e%5c%2e%2e%5cwindows",
    ],
)
def test_asset_path_traversal_is_impossible(publish, logged_in, hostile):
    artifact_id = publish(assets=[("chart.png", PNG)]).json()["id"]

    response = logged_in.get(f"/a/{artifact_id}/assets/{hostile}")

    assert response.status_code in (400, 404), response.status_code
    assert b"root:" not in response.content


def test_one_artifact_cannot_reach_another_artifacts_assets(publish, logged_in):
    first = publish(title="Первый", assets=[("secret.csv", b"very,secret\n")]).json()["id"]
    second = publish(title="Второй").json()["id"]

    assert logged_in.get(f"/a/{second}/assets/secret.csv").status_code == 404
    assert logged_in.get(f"/a/{first}/assets/secret.csv").status_code == 200


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.png",
        "sub/dir.png",
        "back\\slash.png",
        ".hidden",
        "",
        " ",
        "..",
        "a" * 300 + ".png",
        "nul\x00byte.png",
        "‮gnp.exe",
    ],
)
def test_unsafe_asset_filenames_are_rejected_at_publish_time(publish, bad_name):
    response = publish(assets=[(bad_name, PNG)])

    assert response.status_code == 422, response.text
    assert "asset" in response.text.lower()


def test_unknown_asset_is_404(publish, logged_in):
    artifact_id = publish(assets=[("chart.png", PNG)]).json()["id"]

    assert logged_in.get(f"/a/{artifact_id}/assets/missing.png").status_code == 404


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "..",
        ".",
        "/etc/passwd",
        "..\\..\\windows\\win.ini",
        "chart.png/../../../../etc/passwd",
        "sub/dir.png",
        "a/../b.png",
        "\x00",
        "café.png",
        "\u202egnp.exe",
        "con.png ",
        " leading.png",
        "-".join(["x"] * 200) + ".png",
    ],
)
def test_asset_name_validator_rejects_hostile_names_directly(hostile):
    """Independent of any HTTP client's URL normalisation."""
    from artifact_relay.assets import is_safe_asset_name

    assert is_safe_asset_name(hostile) is False


@pytest.mark.parametrize("good", ["chart.png", "a.b.c.svg", "data_2024-05.csv", "F1.WOFF2"])
def test_asset_name_validator_accepts_ordinary_names(good):
    from artifact_relay.assets import is_safe_asset_name

    assert is_safe_asset_name(good) is True
