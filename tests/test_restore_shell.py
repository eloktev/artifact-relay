from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESTORE = ROOT / "scripts" / "restore.sh"

FAKE_DOCKER = r"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
text = " ".join(args)
log = Path(os.environ["FAKE_DOCKER_LOG"])
with log.open("a") as stream:
    stream.write(text + "\n")
scenario = os.environ["FAKE_DOCKER_SCENARIO"]
if "restore_archive.py ready " in text and scenario == "preexisting_recovery":
    raise SystemExit(1)
if "restore_archive.py apply " in text and scenario in {
    "pretransaction_failure", "swap_failure", "incomplete_internal_rollback"
}:
    raise SystemExit(1)
if "restore_archive.py commit " in text and scenario == "commit_failure":
    raise SystemExit(1)
if " stop " in f" {text} ":
    stop_state = Path(os.environ["FAKE_DOCKER_STOP_STATE"])
    stop_count = int(stop_state.read_text()) if stop_state.exists() else 0
    stop_count += 1
    stop_state.write_text(str(stop_count))
    if scenario == "rollback_stop_failure" and stop_count == 2:
        raise SystemExit(1)
if " up " in f" {text} ":
    state = Path(os.environ["FAKE_DOCKER_STATE"])
    count = int(state.read_text()) if state.exists() else 0
    count += 1
    state.write_text(str(count))
    if scenario in {"health_failure", "rollback_stop_failure"} and count == 1:
        raise SystemExit(1)
raise SystemExit(0)
"""


def run_restore(
    tmp_path: Path, scenario: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"test archive")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_STATE": str(tmp_path / "up-count"),
            "FAKE_DOCKER_STOP_STATE": str(tmp_path / "stop-count"),
            "FAKE_DOCKER_SCENARIO": scenario,
        }
    )
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603
        [bash, str(RESTORE), str(archive), "--yes"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log.read_text().splitlines()


def test_preexisting_recovery_fails_before_downtime(tmp_path: Path):
    result, calls = run_restore(tmp_path, "preexisting_recovery")

    assert result.returncode != 0
    assert any("restore_archive.py ready " in call for call in calls)
    assert not any(" stop " in f" {call} " for call in calls)
    assert not any("restore_archive.py apply " in call for call in calls)
    assert not any("restore_archive.py rollback " in call for call in calls)
    assert not any(" up " in f" {call} " for call in calls)


@pytest.mark.parametrize(
    "scenario",
    ["pretransaction_failure", "swap_failure", "incomplete_internal_rollback"],
)
def test_apply_failure_retries_rollback_before_restarting(tmp_path: Path, scenario: str):
    result, calls = run_restore(tmp_path, scenario)

    assert result.returncode != 0
    assert any("restore_archive.py apply " in call for call in calls)
    assert sum("restore_archive.py rollback " in call for call in calls) == 1
    assert sum(" up " in f" {call} " for call in calls) == 1
    assert sum(" stop " in f" {call} " for call in calls) == 1


def test_health_failure_rolls_back_then_restarts_original_service(tmp_path: Path):
    result, calls = run_restore(tmp_path, "health_failure")

    assert result.returncode != 0
    assert sum(" up " in f" {call} " for call in calls) == 2
    assert sum(" stop " in f" {call} " for call in calls) == 2
    assert sum("restore_archive.py rollback " in call for call in calls) == 1
    assert not any("restore_archive.py commit " in call for call in calls)


def test_rollback_is_skipped_when_unhealthy_container_cannot_be_stopped(tmp_path: Path):
    result, calls = run_restore(tmp_path, "rollback_stop_failure")

    assert result.returncode != 0
    assert sum(" up " in f" {call} " for call in calls) == 1
    assert sum(" stop " in f" {call} " for call in calls) == 2
    assert not any("restore_archive.py rollback " in call for call in calls)
    assert "Rollback skipped because application shutdown failed" in result.stderr


def test_commit_failure_never_rolls_back_healthy_restored_data(tmp_path: Path):
    result, calls = run_restore(tmp_path, "commit_failure")

    assert result.returncode != 0
    assert sum(" up " in f" {call} " for call in calls) == 1
    assert sum(" stop " in f" {call} " for call in calls) == 1
    assert sum("restore_archive.py commit " in call for call in calls) == 1
    assert not any("restore_archive.py rollback " in call for call in calls)


def test_successful_health_check_commits_recovery_state(tmp_path: Path):
    result, calls = run_restore(tmp_path, "success")

    assert result.returncode == 0, result.stderr
    assert sum(" up " in f" {call} " for call in calls) == 1
    assert sum("restore_archive.py commit " in call for call in calls) == 1
    assert not any("restore_archive.py rollback " in call for call in calls)
