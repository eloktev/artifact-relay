"""Regression: the committed tree must be the tree the tools actually see.

Two of these are about a file's *tracked* status rather than its contents, which is exactly
the kind of thing a local test run cannot notice: everything passes on the machine where the
file was created and fails on a fresh checkout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> set[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    # Every argument is a literal and the executable is resolved from PATH by `which`;
    # there is no untrusted input in this call.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return {name for name in result.stdout.split("\0") if name}


def test_the_tests_package_marker_is_committed():
    """`tests/*.py` import `tests.conftest`, which only resolves if this file exists.

    Untracked, the suite passes locally and fails on a fresh clone with a bare
    `ModuleNotFoundError: No module named 'tests'` — CI and `uv run pytest` would be looking
    at different trees.
    """
    marker = ROOT / "tests" / "__init__.py"

    assert marker.is_file()
    assert "tests/__init__.py" in tracked_files()


def test_the_suite_really_depends_on_that_marker():
    """Guard the reason above, so the file is not "cleaned up" later as unused."""
    importers = [
        path.name
        for path in (ROOT / "tests").glob("*.py")
        if "from tests." in path.read_text(encoding="utf-8")
    ]

    assert importers, "no test imports `tests.…`; the package marker may be removable"


def test_no_stray_scratch_files_are_committed():
    committed = tracked_files()
    strays = [
        name
        for name in committed
        if Path(name).name.startswith(("antml_tmp", "tmp", "scratch", ".DS_Store"))
        or name.endswith((".orig", ".rej", ".bak"))
    ]

    assert strays == [], strays
    assert not (ROOT / "src" / "antml_tmp").exists()


def test_every_committed_python_file_lives_under_a_package_or_a_known_root():
    """A zero-byte file with no suffix under `src/` is a mistake, not a module."""
    offenders = [
        name for name in tracked_files() if name.startswith("src/") and "." not in Path(name).name
    ]

    assert offenders == [], offenders


def test_runtime_image_uses_python_healthcheck_without_extra_curl_package():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "urllib.request.urlopen" in dockerfile
    assert "apt-get install" not in dockerfile
    assert "python:3.12-slim-bookworm@sha256:" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.7.0@sha256:" in dockerfile
