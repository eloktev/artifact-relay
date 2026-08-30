import io
import logging

import pytest
from PIL import Image

from tests.conftest import API_TOKEN

# --- request size guard ------------------------------------------------------


def test_declared_content_length_over_the_ceiling_is_refused(settings):
    from fastapi import HTTPException
    from starlette.datastructures import Headers
    from starlette.requests import Request

    from artifact_relay.dependencies import enforce_request_size
    from artifact_relay.validation import max_request_bytes

    def fake_request(headers: dict[str, str]) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/artifacts",
            "headers": Headers(headers).raw,
            "app": type("A", (), {"state": type("S", (), {"settings": settings})()})(),
        }
        return Request(scope)

    ceiling = max_request_bytes(settings)

    # Just under the ceiling passes.
    enforce_request_size(fake_request({"content-length": str(ceiling)}))
    # A missing or non-numeric header cannot be trusted either way; fall through to the
    # per-field limits rather than rejecting a legitimate chunked upload.
    enforce_request_size(fake_request({}))
    enforce_request_size(fake_request({"content-length": "not-a-number"}))

    with pytest.raises(HTTPException) as excinfo:
        enforce_request_size(fake_request({"content-length": str(ceiling + 1)}))
    assert excinfo.value.status_code == 413


# --- configuration validation ------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("session_secret_key", "s" * 31), ("artifact_api_token", "t" * 15)],
)
def test_weak_secrets_are_refused_at_startup(tmp_path, field, value):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "artifact_api_token": "t" * 32,
        "view_password_hash": "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        "session_secret_key": "s" * 48,
        field: value,
    }

    with pytest.raises(ValidationError) as excinfo:
        Settings(**kwargs)  # type: ignore[arg-type]

    assert field.upper() in str(excinfo.value).upper()


@pytest.mark.parametrize(
    ("field", "placeholder"),
    [
        ("artifact_api_token", "replace-me-with-at-least-16-random-characters"),
        ("session_secret_key", "replace-me-with-at-least-32-random-characters"),
    ],
)
def test_documented_placeholder_credentials_are_refused_at_startup(tmp_path, field, placeholder):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "artifact_api_token": "t" * 32,
        "view_password_hash": "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        "session_secret_key": "s" * 48,
        field: placeholder,
    }

    with pytest.raises(ValidationError) as excinfo:
        Settings(**kwargs)  # type: ignore[arg-type]

    assert "PLACEHOLDER" in str(excinfo.value).upper()


@pytest.mark.parametrize(
    "password_hash",
    [
        "not-an-argon2-hash",
        "$argon2i$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        "$argon2id$v=19$m=broken,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
    ],
)
def test_malformed_or_non_argon2id_password_hash_is_refused_at_startup(tmp_path, password_hash):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    with pytest.raises(ValidationError) as excinfo:
        Settings(
            data_dir=tmp_path,
            artifact_api_token="t" * 32,
            view_password_hash=password_hash,
            session_secret_key="s" * 48,
        )

    assert "ARGON2ID" in str(excinfo.value).upper()


def test_share_links_are_disabled_by_default(tmp_path):
    from artifact_relay.config import Settings

    settings = Settings(
        data_dir=tmp_path,
        artifact_api_token="t" * 32,
        view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        session_secret_key="s" * 48,
    )

    assert settings.share_links_enabled is False


def test_enabled_share_links_require_an_https_base_url(tmp_path):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    with pytest.raises(ValidationError) as excinfo:
        Settings(
            data_dir=tmp_path,
            base_url="http://vps.example.test:8000",
            share_links_enabled=True,
            artifact_api_token="t" * 32,
            view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
            session_secret_key="s" * 48,
        )

    message = str(excinfo.value).upper()
    assert "SHARE_LINKS_ENABLED" in message
    assert "HTTPS" in message


@pytest.mark.parametrize(
    "base_url",
    [
        "https:publisher.example",
        "https://",
        "https://user:password@publisher.example",
        "https://publisher.example/path",
        "https://publisher.example?query=1",
        "https://publisher.example#fragment",
        "http://publisher.example",
    ],
)
def test_base_url_must_be_a_canonical_secure_origin(tmp_path, base_url):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    with pytest.raises(ValidationError, match="BASE_URL"):
        Settings(
            data_dir=tmp_path,
            base_url=base_url,
            artifact_api_token="t" * 32,
            view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
            session_secret_key="s" * 48,
        )


def test_https_base_url_requires_secure_cookies(tmp_path):
    from pydantic import ValidationError

    from artifact_relay.config import Settings

    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(
            data_dir=tmp_path,
            base_url="https://publisher.example",
            cookie_secure=False,
            artifact_api_token="t" * 32,
            view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
            session_secret_key="s" * 48,
        )


def test_plain_http_is_allowed_only_for_loopback(tmp_path):
    from artifact_relay.config import Settings

    settings = Settings(
        data_dir=tmp_path,
        base_url="http://127.0.0.1:8000",
        cookie_secure=False,
        artifact_api_token="t" * 32,
        view_password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
        session_secret_key="s" * 48,
    )
    assert settings.base_url == "http://127.0.0.1:8000"


# --- Open Graph card edge cases ----------------------------------------------


def test_long_titles_are_truncated_with_an_ellipsis_not_overflowed():
    from artifact_relay.ogimage import HEIGHT, WIDTH, render_card

    long_title = " ".join(["Совершенно необъятный заголовок"] * 30)
    png = render_card(title=long_title, kind="markdown", created="1 мая 2026")

    image = Image.open(io.BytesIO(png))
    assert image.size == (WIDTH, HEIGHT)

    short = render_card(title="Короткий", kind="markdown", created="1 мая 2026")
    assert png != short


def test_a_single_unbreakable_word_still_renders():
    from artifact_relay.ogimage import render_card

    png = render_card(title="а" * 400, kind="html", created="1 мая 2026")

    assert Image.open(io.BytesIO(png)).size == (1200, 630)


# --- rendering fallbacks -----------------------------------------------------


def test_unknown_fence_language_falls_back_to_escaped_plain_text():
    from artifact_relay.rendering import render_markdown

    html = render_markdown("```definitely-not-a-language\n<b>&x</b>\n```\n").html

    assert "<pre>" in html
    assert "&lt;b&gt;" in html
    assert "<b>&x</b>" not in html


def test_a_fence_with_no_language_is_still_escaped():
    from artifact_relay.rendering import render_markdown

    html = render_markdown("```\n<script>alert(1)</script>\n```\n").html

    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_mermaid_diagram_source_is_escaped_not_executed():
    from artifact_relay.rendering import render_markdown

    result = render_markdown('```mermaid\ngraph TD\n  A["<img src=x onerror=alert(1)>"]\n```\n')

    assert result.has_mermaid is True
    assert "onerror=alert" not in result.html or "&lt;img" in result.html
    assert "<img src=x" not in result.html


def test_a_document_with_no_headings_has_an_empty_toc():
    from artifact_relay.rendering import render_markdown

    assert render_markdown("Просто абзац.\n").toc == []


# --- download filenames ------------------------------------------------------


def test_a_fully_cyrillic_title_still_yields_an_ascii_fallback_filename():
    from datetime import UTC, datetime

    from artifact_relay.download import content_disposition
    from artifact_relay.models import Artifact

    artifact = Artifact(
        id="A" * 32,
        title="Отчёт",
        summary=None,
        format="markdown",
        source_filename="s.md",
        content_bytes=3,
        created_at=datetime.now(UTC),
        expires_at=None,
    )

    header = content_disposition(artifact)

    assert header.startswith("attachment; ")
    ascii_part = header.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part.isascii() and ascii_part.endswith(".md")
    assert "filename*=UTF-8''" in header
    assert "%D0%9E" in header  # the real Cyrillic name, percent-encoded


# --- validation branches -----------------------------------------------------


def test_a_whitespace_only_summary_is_stored_as_absent(client):
    response = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "T", "format": "markdown", "summary": "   "},
        files={"content": ("s.md", b"# hi\n", "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["summary"] is None


def test_duplicate_asset_filenames_are_rejected(client):
    response = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data={"title": "T", "format": "markdown"},
        files=[
            ("content", ("s.md", b"# hi\n", "text/plain")),
            ("assets", ("chart.png", b"a", "application/octet-stream")),
            ("assets", ("chart.png", b"b", "application/octet-stream")),
        ],
    )

    assert response.status_code == 422
    assert "duplicate" in response.text.lower()


# --- access log on the failure path -----------------------------------------


def test_an_unhandled_error_is_logged_and_never_leaks_internals(settings):
    from fastapi.testclient import TestClient

    from artifact_relay.app import create_app
    from artifact_relay.logging_setup import build_handler

    app = create_app(settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"internal detail {settings.artifact_api_token.get_secret_value()}")

    stream = io.StringIO()
    handler = build_handler(settings, stream=stream)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    try:
        with TestClient(app, base_url=settings.base_url, raise_server_exceptions=False) as client:
            response = client.get("/boom")
    finally:
        logging.getLogger().removeHandler(handler)

    assert response.status_code == 500
    assert "internal detail" not in response.text

    output = stream.getvalue()
    assert "request failed" in output
    assert settings.artifact_api_token.get_secret_value() not in output
    assert "***" in output


# --- hashing CLI -------------------------------------------------------------


def test_hashing_cli_rejects_a_short_password(monkeypatch, capsys):
    from artifact_relay import hashing

    monkeypatch.setattr(hashing.getpass, "getpass", lambda _prompt: "short")

    assert hashing.main() == 2
    assert "короче" in capsys.readouterr().err


def test_hashing_cli_rejects_a_mismatch(monkeypatch, capsys):
    from artifact_relay import hashing

    answers = iter(["достаточно длинный пароль", "другой достаточно длинный"])
    monkeypatch.setattr(hashing.getpass, "getpass", lambda _prompt: next(answers))

    assert hashing.main() == 2
    assert "не совпадают" in capsys.readouterr().err


def test_hashing_cli_prints_only_the_hash(monkeypatch, capsys):
    from artifact_relay import hashing

    password = "достаточно длинный пароль"
    monkeypatch.setattr(hashing.getpass, "getpass", lambda _prompt: password)

    assert hashing.main() == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("$argon2id$")
    assert password not in out
