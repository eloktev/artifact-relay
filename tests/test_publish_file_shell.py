from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISH_FILE = ROOT / "scripts" / "publish-file.sh"
GITHUB_ACTION_PUBLISH = ROOT / "scripts" / "github-action-publish.sh"
SECRET = "test-token-that-must-stay-private"
FAKE_CURL = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

record = {"args": sys.argv[1:], "config": sys.stdin.read()}
Path(os.environ["FAKE_CURL_LOG"]).write_text(json.dumps(record))
if os.environ.get("FAKE_CURL_FAIL"):
    print(os.environ["ARTIFACT_RELAY_API_TOKEN"], file=sys.stderr)
    print(json.dumps({"detail": os.environ["ARTIFACT_RELAY_API_TOKEN"]}))
    raise SystemExit(22)
default_origin = os.environ.get("ARTIFACT_RELAY_BASE_URL", "http://localhost:8000").rstrip("/")
print(os.environ.get("FAKE_CURL_RESPONSE", json.dumps({"url": default_origin + "/a/abc123"})))
"""


def run_publish(
    tmp_path: Path,
    args: list[str],
    *,
    token: str | None = SECRET,
    base_url: str | None = "https://relay.example",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    log = tmp_path / "curl.json"
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_CURL_LOG"] = str(log)
    if token is None:
        env.pop("ARTIFACT_RELAY_API_TOKEN", None)
    else:
        env["ARTIFACT_RELAY_API_TOKEN"] = token
    if base_url is None:
        env.pop("ARTIFACT_RELAY_BASE_URL", None)
    else:
        env["ARTIFACT_RELAY_BASE_URL"] = base_url
    if extra_env:
        env.update(extra_env)

    shell = shutil.which("sh")
    assert shell is not None
    result = subprocess.run(  # noqa: S603
        [shell, str(PUBLISH_FILE), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    record = json.loads(log.read_text()) if log.exists() else None
    return result, record


def test_markdown_publish_uses_expected_multipart_request_without_secret_in_arguments(
    tmp_path: Path,
) -> None:
    source = tmp_path / "release report.md"
    source.write_text("# Ready\n")

    result, record = run_publish(tmp_path, ["--title", "Release report", str(source)])

    assert result.returncode == 0, result.stderr
    assert result.stdout == "https://relay.example/a/abc123\n"
    assert result.stderr == ""
    assert record is not None
    args = record["args"]
    assert isinstance(args, list)
    assert args == [
        "--fail",
        "--silent",
        "--show-error",
        "--request",
        "POST",
        "--form-string",
        "title=Release report",
        "--form-string",
        "format=markdown",
        "--form-string",
        "expires_in_days=30",
        "--form",
        f"content=@{source};type=text/markdown",
        "https://relay.example/api/artifacts",
        "--config",
        "-",
    ]
    assert record["config"] == f'header = "Authorization: Bearer {SECRET}"\n'
    assert SECRET not in json.dumps(args)
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_html_extension_selects_html_format_and_mime_type(tmp_path: Path) -> None:
    source = tmp_path / "demo.HtMl"
    source.write_text("<!doctype html><title>Demo</title>")

    result, record = run_publish(tmp_path, [str(source)])

    assert result.returncode == 0, result.stderr
    assert record is not None
    args = record["args"]
    assert isinstance(args, list)
    assert "title=demo" in args
    assert "format=html" in args
    assert f"content=@{source};type=text/html" in args


def test_explicit_format_summary_and_expiry_are_sent(tmp_path: Path) -> None:
    source = tmp_path / "artifact.txt"
    source.write_text("# Explicit Markdown\n")

    result, record = run_publish(
        tmp_path,
        [
            "--format",
            "markdown",
            "--summary",
            "Agent result",
            "--expires-in-days",
            "0",
            str(source),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert record is not None
    args = record["args"]
    assert isinstance(args, list)
    assert "format=markdown" in args
    assert "summary=Agent result" in args
    assert "expires_in_days=0" in args
    assert f"content=@{source};type=text/markdown" in args


def test_comma_filename_cannot_make_real_curl_upload_sibling_files(tmp_path: Path) -> None:
    requested = tmp_path / "a,b.md"
    requested.write_text("REQUESTED_FILE")
    (tmp_path / "a").write_text("LEAKED_A")
    (tmp_path / "b.md").write_text("LEAKED_B")
    request_bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            request_bodies.append(self.rfile.read(length))
            response = b'{"url":"https://public.example/a/abc123"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        env = dict(os.environ)
        env["ARTIFACT_RELAY_API_TOKEN"] = SECRET
        env["ARTIFACT_RELAY_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(  # noqa: S603
            [str(PUBLISH_FILE), requested.name],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode != 0
    assert "comma" in result.stderr
    assert request_bodies == []


@pytest.mark.parametrize("metacharacter", [",", ";", '"', "\\"])
def test_curl_multipart_metacharacter_filename_is_rejected(
    tmp_path: Path, metacharacter: str
) -> None:
    source = tmp_path / f"a{metacharacter}b.md"
    source.write_text("# Artifact\n")

    result, record = run_publish(tmp_path, [str(source)])

    assert result.returncode != 0
    assert "FILE name" in result.stderr
    assert record is None


@pytest.mark.parametrize(
    ("args", "token", "base_url", "message"),
    [
        (["missing.md"], None, "https://relay.example", "ARTIFACT_RELAY_API_TOKEN"),
        (["missing.md"], SECRET, "ftp://relay.example", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "http://relay.example", "HTTPS unless it addresses loopback"),
        (["missing.md"], SECRET, "https://user@relay.example", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "https://relay.example/path", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "https://relay.example?x=1", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "https://relay.example#fragment", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "https://relay example", "ARTIFACT_RELAY_BASE_URL"),
        (["missing.md"], SECRET, "https://relay.example\n.invalid", "ARTIFACT_RELAY_BASE_URL"),
    ],
)
def test_invalid_environment_fails_before_curl(
    tmp_path: Path,
    args: list[str],
    token: str | None,
    base_url: str,
    message: str,
) -> None:
    source = tmp_path / "artifact.md"
    source.write_text("# Artifact\n")
    args = [str(source) if arg == "missing.md" else arg for arg in args]
    result, record = run_publish(tmp_path, args, token=token, base_url=base_url)

    assert result.returncode != 0
    assert message in result.stderr
    assert record is None
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


@pytest.mark.parametrize(
    ("args", "filename", "message"),
    [
        ([], None, "Usage:"),
        (["missing.md"], None, "readable file"),
        (["artifact.txt"], "artifact.txt", "detect format"),
        (["--format", "pdf", "artifact.md"], "artifact.md", "--format"),
        (["--expires-in-days", "-1", "artifact.md"], "artifact.md", "--expires-in-days"),
        (["--expires-in-days", "tomorrow", "artifact.md"], "artifact.md", "--expires-in-days"),
    ],
)
def test_invalid_arguments_fail_before_curl(
    tmp_path: Path, args: list[str], filename: str | None, message: str
) -> None:
    if filename is not None:
        (tmp_path / filename).write_text("content")
        args = [str(tmp_path / filename) if arg == filename else arg for arg in args]

    result, record = run_publish(tmp_path, args)

    assert result.returncode != 0
    assert message in result.stderr
    assert record is None
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_localhost_is_the_default_base_url(tmp_path: Path) -> None:
    source = tmp_path / "note.markdown"
    source.write_text("# Note\n")

    result, record = run_publish(tmp_path, [str(source)], base_url=None)

    assert result.returncode == 0, result.stderr
    assert record is not None
    args = record["args"]
    assert isinstance(args, list)
    assert "http://localhost:8000/api/artifacts" in args


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_plain_http_base_url_is_allowed_for_loopback(tmp_path: Path, host: str) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")

    result, record = run_publish(tmp_path, [str(source)], base_url=f"http://{host}:8000")

    assert result.returncode == 0, result.stderr
    assert record is not None


def test_prints_public_artifact_url_returned_by_api(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")

    result, _ = run_publish(
        tmp_path,
        [str(source)],
        base_url="http://127.0.0.1:8000",
        extra_env={"FAKE_CURL_RESPONSE": '{"url":"https://public.example/a/abc123"}'},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "https://public.example/a/abc123\n"


def test_rejects_plain_http_public_artifact_url(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")

    result, _ = run_publish(
        tmp_path,
        [str(source)],
        extra_env={"FAKE_CURL_RESPONSE": '{"url":"http://public.example/a/abc123"}'},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "valid artifact URL" in result.stderr


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_accepts_plain_http_loopback_artifact_url(tmp_path: Path, host: str) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")
    url = f"http://{host}:8000/a/abc123"

    result, _ = run_publish(
        tmp_path,
        [str(source)],
        extra_env={"FAKE_CURL_RESPONSE": json.dumps({"url": url})},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{url}\n"


def test_missing_mktemp_fails_with_runtime_dependency_message(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")
    fake_bin = tmp_path / "runtime-bin"
    fake_bin.mkdir()
    python3 = shutil.which("python3")
    curl = shutil.which("curl")
    assert python3 is not None
    assert curl is not None
    (fake_bin / "python3").symlink_to(python3)
    (fake_bin / "curl").symlink_to(curl)

    result, record = run_publish(
        tmp_path,
        [str(source)],
        extra_env={"PATH": str(fake_bin)},
    )

    assert result.returncode != 0
    assert "Required command not found: mktemp" in result.stderr
    assert record is None


def test_cleanup_trap_uses_posix_signal_zero() -> None:
    script = PUBLISH_FILE.read_text()

    assert 'trap \'rm -f "$response_file" "$error_file"\' 0 HUP INT TERM' in script
    assert " EXIT " not in script


def test_curl_failure_does_not_relay_secret_bearing_output(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")

    result, _ = run_publish(tmp_path, [str(source)], extra_env={"FAKE_CURL_FAIL": "1"})

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Publish failed" in result.stderr
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_malformed_success_response_fails_without_printing_response(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n")

    result, _ = run_publish(
        tmp_path,
        [str(source)],
        extra_env={"FAKE_CURL_RESPONSE": f'{{"detail":"{SECRET}"}}'},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "valid artifact URL" in result.stderr
    assert SECRET not in result.stderr


def test_github_action_wrapper_publishes_and_writes_only_validated_url(tmp_path: Path) -> None:
    source = tmp_path / "release report.md"
    source.write_text("# Release ready\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    curl_log = tmp_path / "curl.json"
    output = tmp_path / "github-output"
    env = dict(os.environ) | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_CURL_LOG": str(curl_log),
        "ARTIFACT_RELAY_API_TOKEN": SECRET,
        "ARTIFACT_RELAY_BASE_URL": "https://relay.example",
        "INPUT_ARTIFACT_PATH": str(source),
        "INPUT_TITLE": "Release report",
        "INPUT_SUMMARY": "CI result",
        "INPUT_FORMAT": "markdown",
        "INPUT_EXPIRES_IN_DAYS": "7",
        "GITHUB_OUTPUT": str(output),
    }

    result = subprocess.run(  # noqa: S603
        [str(GITHUB_ACTION_PUBLISH)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert output.read_text() == "artifact-url=https://relay.example/a/abc123\n"
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    assert SECRET not in output.read_text()
    request = json.loads(curl_log.read_text())
    assert "title=Release report" in request["args"]
    assert "summary=CI result" in request["args"]
    assert "format=markdown" in request["args"]
    assert "expires_in_days=7" in request["args"]


def test_github_action_metadata_uses_composite_wrapper_without_token_input() -> None:
    metadata = (ROOT / "action.yml").read_text()

    assert "using: composite" in metadata
    assert "scripts/github-action-publish.sh" in metadata
    assert "artifact-url:" in metadata
    assert "api-token:" not in metadata
    assert "ARTIFACT_RELAY_API_TOKEN" not in metadata
