"""Regression tests for the release-only GHCR publication path."""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-ghcr.yml"


class ActionsLoader(yaml.SafeLoader):
    """Parse Actions YAML 1.2-style, where `on` is a string rather than a boolean."""


ActionsLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class ComposeLoader(yaml.SafeLoader):
    """Understand Compose's `!reset` override tag for structural assertions."""


ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def load_yaml(text: str, loader_type: type[yaml.SafeLoader]) -> Any:
    loader = loader_type(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_workflow() -> dict[str, Any]:
    loaded = load_yaml(WORKFLOW.read_text(encoding="utf-8"), ActionsLoader)
    assert isinstance(loaded, dict)
    return loaded


def publish_job() -> dict[str, Any]:
    jobs = load_workflow()["jobs"]
    assert list(jobs) == ["publish"]
    return jobs["publish"]


def step_using(action: str) -> dict[str, Any]:
    for step in publish_job()["steps"]:
        if str(step.get("uses", "")).startswith(f"{action}@"):
            return step
    raise AssertionError(f"missing {action} step")


def test_publication_runs_only_for_v_prefixed_tag_pushes():
    workflow = load_workflow()

    assert workflow["on"] == {"push": {"tags": ["v*"]}}
    assert "github.repository == 'eloktev/artifact-relay'" in publish_job()["if"]
    assert "github.ref_type == 'tag'" in publish_job()["if"]
    assert "startsWith(github.ref_name, 'v')" in publish_job()["if"]


def test_publication_has_only_the_permissions_it_needs():
    assert load_workflow()["permissions"] == {"contents": "read", "packages": "write"}


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [step["uses"] for step in publish_job()["steps"] if "uses" in step]

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses), uses


def test_registry_login_uses_only_the_scoped_github_token():
    login = step_using("docker/login-action")

    assert login["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ secrets.GITHUB_TOKEN }}",
    }


def test_metadata_derives_only_version_and_full_sha_tags_without_latest():
    metadata = step_using("docker/metadata-action")["with"]
    tag_rules = metadata["tags"].splitlines()

    assert metadata["images"] == "ghcr.io/eloktev/artifact-relay"
    assert tag_rules == ["type=ref,event=tag", "type=sha,format=long"]
    assert metadata["flavor"] == "latest=false"
    assert ":latest" not in WORKFLOW.read_text(encoding="utf-8")


def test_build_publishes_multi_arch_with_provenance_and_sbom():
    build = step_using("docker/build-push-action")["with"]

    assert build["push"] == "true"
    assert build["platforms"] == "linux/amd64,linux/arm64"
    assert build["provenance"] == "mode=max"
    assert build["sbom"] == "true"
    assert build["tags"] == "${{ steps.meta.outputs.tags }}"
    assert build["labels"] == "${{ steps.meta.outputs.labels }}"


def test_ghcr_compose_override_requires_an_explicit_digest():
    override_path = ROOT / "deploy" / "compose.ghcr.yml"
    override = load_yaml(override_path.read_text(encoding="utf-8"), ComposeLoader)
    app = override["services"]["app"]

    assert app["image"].startswith("ghcr.io/eloktev/artifact-relay@sha256:")
    assert "${ARTIFACT_RELAY_DIGEST:?" in app["image"]
    assert "vX.Y.Z" not in app["image"]
    assert app["build"] is None
    assert app["pull_policy"] == "missing"


def run_release_validation(
    tag: str, pyproject: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["python", str(ROOT / "deploy" / "validate_release.py"), tag]
    if pyproject is not None:
        command.append(str(pyproject))
    return subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=False
    )


def test_release_validation_accepts_exact_matching_semver() -> None:
    result = run_release_validation("v1.1.0")

    assert result.returncode == 0, result.stderr


def test_release_validation_rejects_malformed_semver() -> None:
    for tag in ("v1.0", "v1.0.0-rc1", "v01.0.0", "release-1.0.0", "v1.0.0junk"):
        result = run_release_validation(tag)
        assert result.returncode != 0, tag
        assert "strict SemVer" in result.stderr


def test_release_validation_rejects_pyproject_version_mismatch(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "artifact-relay"\nversion = "2.0.0"\n')

    result = run_release_validation("v1.0.0", pyproject)

    assert result.returncode != 0
    assert "does not match pyproject version v2.0.0" in result.stderr


def test_release_validation_runs_before_registry_login() -> None:
    steps = publish_job()["steps"]
    validation_index = next(
        index for index, step in enumerate(steps) if "validate_release.py" in step.get("run", "")
    )
    login_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("docker/login-action@")
    )

    assert validation_index < login_index


def test_managed_preflight_rejects_non_sha256_digest(tmp_path: Path) -> None:
    tenant_env = tmp_path / "tenant.env"
    tenant_env.write_text("BASE_URL='https://tenant.relay.lok-labs.com'\n")
    command = [
        "python",
        str(ROOT / "deploy" / "validate_managed_deployment.py"),
        "not-a-digest",
        str(tenant_env),
    ]

    result = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "64 lowercase hexadecimal" in result.stderr


def test_managed_compose_and_documented_command_consume_tenant_env() -> None:
    managed = (ROOT / "deploy" / "compose.managed.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "ARTIFACT_RELAY_TENANT_ENV" in managed
    assert "BASE_URL: ${BASE_URL:?" in managed
    assert 'COOKIE_SECURE: "true"' in managed
    assert 'SHARE_LINKS_ENABLED: "false"' in managed
    assert "127.0.0.1:${ARTIFACT_RELAY_PORT:-8000}:8000" in managed
    assert "artifact-data:/data" in managed
    assert "docker buildx imagetools inspect ghcr.io/eloktev/artifact-relay:vX.Y.Z" in readme
    assert "validate_managed_deployment.py" in readme
    assert "--env-file /secure/tenant.env" in readme
    assert "deploy/compose.managed.yml" in readme


def test_documentation_keeps_source_build_and_requires_ghcr_digest():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    default_compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert default_compose["services"]["app"]["build"] == {"context": "."}
    assert "docker build -t artifact-relay:1.1.0 ." in readme
    assert "deploy/compose.ghcr.yml" in readme
    assert "ghcr.io/eloktev/artifact-relay@sha256:" in readme
    assert "No GHCR image has been published yet" not in readme
    assert "ghcr.io/eloktev/artifact-relay:v1.1.0" in readme
    assert "ghcr.io/eloktev/artifact-relay:latest" not in readme


def test_managed_upgrade_runbook_pins_backup_upgrade_and_rollback_digests() -> None:
    runbook = (ROOT / "docs" / "UPGRADE_ROLLBACK.md").read_text(encoding="utf-8")
    managed_layers = " ".join(
        ("-f docker-compose.yml", "-f deploy/compose.ghcr.yml", "-f deploy/compose.managed.yml")
    )

    assert "PREVIOUS_ARTIFACT_RELAY_DIGEST" in runbook
    assert "NEW_ARTIFACT_RELAY_DIGEST" in runbook
    assert "PRE_UPGRADE_BACKUP" in runbook
    assert "previous-digest" in runbook
    assert "managed-project" in runbook
    assert "tenant-env" in runbook
    assert runbook.count(managed_layers) >= 3
    assert "up -d --wait --wait-timeout 120 app" in runbook
    assert 'ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST"' in runbook
    assert 'MANAGED=1 ./scripts/backup.sh "$PRE_UPGRADE_BACKUP/data"' in runbook
    assert (
        "MANAGED=1 ./scripts/restore.sh "
        '"$PRE_UPGRADE_BACKUP/data/artifact-relay-data.tar.gz"' in runbook
    )
    assert "ARTIFACT_RELAY_PROJECT" in runbook
    assert "curl --fail --silent --show-error --connect-timeout 5 --max-time 15" in runbook


def test_local_managed_upgrade_rollback_smoke_is_executable_and_uses_three_layers() -> None:
    smoke = ROOT / "scripts" / "smoke-managed-upgrade-rollback.sh"
    content = smoke.read_text(encoding="utf-8")

    assert stat.S_IMODE(smoke.stat().st_mode) & stat.S_IXUSR
    assert "PREVIOUS_ARTIFACT_RELAY_DIGEST" in content
    assert "NEW_ARTIFACT_RELAY_DIGEST" in content
    assert "PRE_UPGRADE_BACKUP" in content
    assert content.count('-f "$BASE_COMPOSE" -f "$IMAGE_COMPOSE" -f "$MANAGED_COMPOSE"') >= 3
    assert "up -d --wait --wait-timeout 120 app" in content
    assert 'ARTIFACT_RELAY_DIGEST="$PREVIOUS_ARTIFACT_RELAY_DIGEST"' in content
    cleanup = content.split("cleanup() {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert 'ARTIFACT_RELAY_DIGEST="${PREVIOUS_ARTIFACT_RELAY_DIGEST:-' in cleanup


def test_managed_backup_restore_smoke_runs_real_scripts_on_production_layers() -> None:
    smoke = ROOT / "scripts" / "smoke-managed-backup-restore.sh"
    content = smoke.read_text(encoding="utf-8")

    assert stat.S_IMODE(smoke.stat().st_mode) & stat.S_IXUSR
    assert "docker-compose.yml" in content
    assert "deploy/compose.ghcr.yml" in content
    assert "deploy/compose.managed.yml" in content
    assert 'MANAGED=1 "$ROOT/scripts/backup.sh"' in content
    assert 'MANAGED=1 "$ROOT/scripts/restore.sh"' in content
    assert "ARTIFACT_RELAY_TENANT_ENV" in content
    assert "ARTIFACT_RELAY_PROJECT" in content
    assert "ARTIFACT_RELAY_DIGEST" in content
    assert "ORIGINAL_IMAGE" in content
    assert "ORIGINAL_ENV" in content
    assert "--timeout 10" in content
