import hashlib
import re
from pathlib import Path

from artifact_relay.templating import STATIC_DIR

MERMAID = STATIC_DIR / "js" / "mermaid.min.js"
VENDOR_DOC = STATIC_DIR / "js" / "VENDOR.md"


def test_mermaid_bundle_is_present_and_substantial():
    assert MERMAID.is_file(), "the Mermaid bundle is not vendored"
    assert MERMAID.stat().st_size > 500_000, "the vendored bundle looks truncated"


def test_vendor_doc_records_the_exact_bundle_that_is_committed():
    digest = hashlib.sha256(MERMAID.read_bytes()).hexdigest()

    assert digest in VENDOR_DOC.read_text(encoding="utf-8"), (
        "static/js/VENDOR.md does not record the SHA-256 of the committed bundle"
    )
    assert re.search(r"\b11\.\d+\.\d+\b", VENDOR_DOC.read_text(encoding="utf-8"))


def test_bundle_needs_no_unsafe_eval_and_makes_no_remote_requests():
    source = MERMAID.read_text(encoding="utf-8", errors="replace")

    assert "new Function" not in source
    assert "eval(" not in source
    assert not re.search(r"\bimport\(", source), "a dynamic import would need a second file"


def test_static_mount_serves_the_bundle_and_the_fonts(client):
    response = client.get("/static/js/mermaid.min.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"

    for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        assert client.get(f"/static/fonts/{font}").status_code == 200

    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/css/highlight.css").status_code == 200
    assert client.get("/static/js/artifact.js").status_code == 200


def test_no_stylesheet_or_script_references_a_remote_origin():
    for path in (STATIC_DIR / "css").glob("*.css"):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text
        assert "https://" not in text

    js = (STATIC_DIR / "js" / "artifact.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js


def test_mermaid_license_is_shipped_alongside_the_bundle():
    license_file = STATIC_DIR / "js" / "Mermaid-LICENSE.txt"

    assert license_file.is_file()
    text = license_file.read_text(encoding="utf-8")
    assert "Copyright (c) 2014-2025 Knut Sveidqvist" in text
    assert "MIT License" in text


def test_font_licence_is_shipped_alongside_the_fonts():
    licence = STATIC_DIR / "fonts" / "DejaVu-LICENSE.txt"

    assert licence.is_file()
    assert "Bitstream Vera" in licence.read_text(encoding="utf-8", errors="replace")


def test_generated_highlight_css_matches_its_generator():
    from artifact_relay.build_assets import TARGET, build

    assert TARGET.read_text(encoding="utf-8") == build(), (
        "run `python -m artifact_relay.build_assets`"
    )


SECRET_SHAPES = re.compile(
    r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"
    r"|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9]{24,}"
)

# The only Argon2 hashes allowed in the tree are unmistakable placeholders: the salt decodes
# to "saltsalt", the digest is all zeros and the cost parameters are absurdly low.
PLACEHOLDER_HASH = "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000"
SCANNED_SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".css",
    ".html",
    ".sh",
    ".txt",
    ".example",
}
SKIP_DIRS = {".git", ".venv", ".ruff_cache", ".pytest_cache", "__pycache__", "data", "fonts"}


def repository_files():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and (path.suffix in SCANNED_SUFFIXES or path.name == "Dockerfile"):
            yield path


def test_env_example_holds_only_placeholders():
    root = Path(__file__).resolve().parents[1]
    example = (root / ".env.example").read_text(encoding="utf-8")

    assert "replace-me" in example
    assert "REPLACE" in example
    assert ".env" in (root / ".gitignore").read_text(encoding="utf-8")


def test_no_repository_file_contains_a_secret_shaped_string():
    offenders = [
        str(path)
        for path in repository_files()
        if SECRET_SHAPES.search(path.read_text(encoding="utf-8", errors="replace"))
    ]

    assert offenders == [], offenders


def test_no_committed_argon2_literal_carries_a_real_digest():
    """Every `$argon2...` string in the tree must be inert.

    `argon2-cffi` reports a malformed hash and a wrong password with the same
    `VerificationError`, so the check is structural instead: a genuine encoded hash has six
    `$`-separated fields ending in a digest. Truncated fixtures (fewer fields) cannot
    authenticate anyone, and the one full-length placeholder has an all-zero digest, for
    which finding a matching password is computationally infeasible.
    """
    pattern = re.compile(r"\$argon2[a-z0-9]*\$[^\s\"'\\]*")
    offenders: list[str] = []

    for path in repository_files():
        for literal in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
            fields = literal.split("$")
            if len(fields) < 6:
                continue  # truncated fixture: structurally incapable of verifying
            digest = fields[5]
            if set(digest) <= {"0"} or "REPLACE" in literal:
                continue  # documented placeholder
            offenders.append(f"{path}: {literal}")

    assert offenders == [], offenders
