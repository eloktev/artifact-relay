from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore.sh"

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a") as stream:
    stream.write(json.dumps(args) + "\n")
if "ps" in args:
    print("running-app")
raise SystemExit(0)
"""


def managed_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    tenant_env = tmp_path / "tenant.env"
    tenant_env.write_text(
        "BASE_URL=https://tenant.example.com\n"
        "SESSION_SECRET=managed-session-secret\n"
        "PASSWORD_HASH=managed-password-hash\n"
    )
    log = tmp_path / "docker.log"
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "MANAGED": "1",
            "ARTIFACT_RELAY_TENANT_ENV": str(tenant_env),
            "ARTIFACT_RELAY_PROJECT": "tenant-production",
            "ARTIFACT_RELAY_DIGEST": "a" * 64,
        }
    )
    return env, log


def run_script(
    script: Path, args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603
        [bash, str(script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def docker_calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def assert_exact_managed_invocation(calls: list[list[str]], tenant_env: Path) -> None:
    expected = [
        "compose",
        "--env-file",
        str(tenant_env),
        "--project-name",
        "tenant-production",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose.ghcr.yml",
        "-f",
        "deploy/compose.managed.yml",
    ]
    assert calls
    assert all(call[: len(expected)] == expected for call in calls), calls


def test_backup_reuses_exact_managed_compose_invocation_for_every_operation(tmp_path: Path) -> None:
    env, log = managed_env(tmp_path)
    destination = tmp_path / "backup"

    result = run_script(BACKUP, [str(destination)], env)

    assert result.returncode == 0, result.stderr
    calls = docker_calls(log)
    assert_exact_managed_invocation(calls, Path(env["ARTIFACT_RELAY_TENANT_ENV"]))
    assert {"ps", "stop", "run", "up"}.issubset({arg for call in calls for arg in call})


def test_restore_reuses_exact_managed_compose_invocation_for_every_operation(
    tmp_path: Path,
) -> None:
    env, log = managed_env(tmp_path)
    archive = tmp_path / "artifact-relay-data.tar.gz"
    archive.write_bytes(b"fixture")

    result = run_script(RESTORE, [str(archive), "--yes"], env)

    assert result.returncode == 0, result.stderr
    calls = docker_calls(log)
    assert_exact_managed_invocation(calls, Path(env["ARTIFACT_RELAY_TENANT_ENV"]))
    assert {"stop", "run", "up"}.issubset({arg for call in calls for arg in call})
    for operation in ("validate", "ready", "apply", "commit"):
        assert any(operation in call for call in calls)


@pytest.mark.parametrize("script", [BACKUP, RESTORE])
@pytest.mark.parametrize(
    "missing",
    ["ARTIFACT_RELAY_TENANT_ENV", "ARTIFACT_RELAY_PROJECT", "ARTIFACT_RELAY_DIGEST"],
)
def test_managed_operations_fail_closed_before_docker_when_required_variable_is_absent(
    tmp_path: Path, script: Path, missing: str
) -> None:
    env, log = managed_env(tmp_path)
    del env[missing]
    args = [str(tmp_path / "backup")]
    if script == RESTORE:
        archive = tmp_path / "artifact-relay-data.tar.gz"
        archive.write_bytes(b"fixture")
        args = [str(archive), "--yes"]

    result = run_script(script, args, env)

    assert result.returncode != 0
    assert missing in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("script", [BACKUP, RESTORE])
@pytest.mark.parametrize(
    ("vps", "expected_prefix"),
    [
        (False, ["compose", "-f", "docker-compose.yml"]),
        (
            True,
            [
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "deploy/compose.vps.yml",
            ],
        ),
    ],
)
def test_source_and_vps_compose_invocations_remain_compatible(
    tmp_path: Path, script: Path, vps: bool, expected_prefix: list[str]
) -> None:
    env, log = managed_env(tmp_path)
    del env["MANAGED"]
    if vps:
        env["VPS"] = "1"
    args = [str(tmp_path / "backup")]
    if script == RESTORE:
        archive = tmp_path / "artifact-relay-data.tar.gz"
        archive.write_bytes(b"fixture")
        args = [str(archive), "--yes"]

    result = run_script(script, args, env)

    assert result.returncode == 0, result.stderr
    calls = docker_calls(log)
    assert all(call[: len(expected_prefix)] == expected_prefix for call in calls)
