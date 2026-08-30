#!/usr/bin/env python3
"""Fail closed unless a release tag exactly matches the project version."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

STRICT_RELEASE_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: validate_release.py vX.Y.Z [pyproject.toml]")
    tag = sys.argv[1]
    if STRICT_RELEASE_TAG.fullmatch(tag) is None:
        raise SystemExit(f"release tag {tag!r} is not strict SemVer vX.Y.Z")

    pyproject = (
        Path(sys.argv[2]) if len(sys.argv) == 3 else Path(__file__).parents[1] / "pyproject.toml"
    )
    with pyproject.open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    expected = f"v{version}"
    if tag != expected:
        raise SystemExit(f"release tag {tag} does not match pyproject version {expected}")


if __name__ == "__main__":
    main()
