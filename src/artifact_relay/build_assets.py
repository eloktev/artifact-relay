"""Regenerate derived static assets (currently the Pygments stylesheet).

Run with ``python -m artifact_relay.build_assets``. The output is committed so that the
Docker image never needs a build step for CSS.
"""

from __future__ import annotations

from pathlib import Path

from artifact_relay.rendering import pygments_stylesheet

LIGHT_STYLE = "default"
DARK_STYLE = "github-dark"
TARGET = Path(__file__).parent / "static" / "css" / "highlight.css"


def build() -> str:
    return "\n".join(
        [
            "/* Generated: python -m artifact_relay.build_assets. Do not edit by hand. */",
            "@media (prefers-color-scheme: light) {",
            pygments_stylesheet(LIGHT_STYLE),
            "}",
            "@media (prefers-color-scheme: dark) {",
            pygments_stylesheet(DARK_STYLE),
            "}",
            "",
        ]
    )


def main() -> None:
    TARGET.write_text(build(), encoding="utf-8")
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
