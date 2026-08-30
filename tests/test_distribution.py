from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_legal_and_community_files_cover_project_and_vendored_assets():
    assert read("LICENSE").startswith("MIT License\n")
    notices = read("THIRD_PARTY_NOTICES.md")
    assert "Mermaid 11.12.0" in notices
    assert "MIT License" in notices
    assert "DejaVu Sans" in notices
    assert "src/artifact_relay/static/fonts/DejaVu-LICENSE.txt" in notices
    assert "Security Policy" in read("SECURITY.md")
    assert "Contributing" in read("CONTRIBUTING.md")


def test_artifact_relay_public_brand_contract():
    project = read("pyproject.toml")
    assert 'name = "artifact-relay"' in project
    assert 'packages = ["src/artifact_relay"]' in project
    assert (ROOT / "src" / "artifact_relay").is_dir()
    legacy_module = "artifact_" + "publisher"
    legacy_brand = "Artifact " + "Publisher"
    assert not (ROOT / "src" / legacy_module).exists()
    assert "Artifact Relay" in read("README.md")
    assert legacy_brand not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "artifact_relay" / "templates").glob("*.html")
    )


def test_local_compose_has_safe_defaults_and_persistent_storage():
    compose = read("docker-compose.yml")
    assert "127.0.0.1:${ARTIFACT_RELAY_PORT:-8000}:8000" in compose
    assert "artifact-data:/data" in compose
    assert "artifact-data:" in compose
    assert "BASE_URL: http://localhost:8000" in compose
    assert 'COOKIE_SECURE: "false"' in compose
    assert 'SHARE_LINKS_ENABLED: "false"' in compose
    assert "/api/health" in compose
    assert "${ARTIFACT_RELAY_IMAGE:-artifact-relay:1.0.0}" in compose


def test_vps_examples_enable_https_cookies_and_shares_behind_caddy():
    override = read("deploy/compose.vps.yml")
    assert "BASE_URL: https://artifacts.example.com" in override
    assert 'COOKIE_SECURE: "true"' in override
    assert 'SHARE_LINKS_ENABLED: "true"' in override
    caddy = read("deploy/Caddyfile.example")
    assert "artifacts.example.com" in caddy
    assert "reverse_proxy 127.0.0.1:8000" in caddy


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log!s}\n"
        f"cat >> {log!s}\n"
        "printf '%s\\n' '$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$0000000000000000000000'\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, log


def test_bootstrap_creates_private_env_and_hashes_password_in_container(tmp_path: Path):
    bin_dir, log = _fake_docker(tmp_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ENV_FILE": str(tmp_path / ".env"),
        "ARTIFACT_RELAY_IMAGE": "artifact-relay:1.0.0",
    }
    result = subprocess.run(  # noqa: S603
        [str(ROOT / "scripts" / "bootstrap.sh")],
        input="correct horse battery staple\ncorrect horse battery staple\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    env_file = tmp_path / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    values = env_file.read_text(encoding="utf-8")
    assert "ARTIFACT_API_TOKEN=" in values
    assert "SESSION_SECRET_KEY=" in values
    assert "VIEW_PASSWORD_HASH='$argon2id$" in values
    assert "BASE_URL=http://localhost:8000" in values
    assert "COOKIE_SECURE=false" in values
    assert "SHARE_LINKS_ENABLED=false" in values
    assert "correct horse battery staple" not in values
    invocation = log.read_text(encoding="utf-8")
    expected = "run --rm -i --entrypoint python artifact-relay:1.0.0 -c"
    assert expected in invocation
    assert invocation.count("correct horse battery staple") == 1


def test_bootstrap_refuses_to_overwrite_existing_env(tmp_path: Path):
    bin_dir, log = _fake_docker(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=me\n", encoding="utf-8")
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ENV_FILE": str(env_file),
    }
    result = subprocess.run(  # noqa: S603
        [str(ROOT / "scripts" / "bootstrap.sh")],
        input="unused\nunused\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "KEEP=me\n"
    assert not log.exists()


def test_distribution_docs_are_generic_and_cover_operations():
    combined = "\n".join(
        read(path)
        for path in (
            "README.md",
            "PRODUCT_BRIEF.md",
            "DEVELOPMENT.md",
            "docs/VPS.md",
            "docs/BACKUP_RESTORE.md",
            "docs/UPGRADE_ROLLBACK.md",
            ".env.example",
            "Dockerfile",
        )
    )
    assert "Coolify" not in combined
    assert "lok-labs" not in combined
    assert "immutable" in read("docs/UPGRADE_ROLLBACK.md").lower()
    assert "backup" in read("docs/BACKUP_RESTORE.md").lower()
    readme = read("README.md")
    assert "eloktev/hermes-artifact-relay" in readme
    assert "ARTIFACT_RELAY_API_TOKEN" in readme
    assert "plugins.entries.artifact-relay.settings.base_url" in readme
    assert "--wait-timeout 120" in read("scripts/restore.sh")
    assert "--wait-timeout 120" in read("scripts/backup.sh")
    dockerfile = read("Dockerfile")
    assert "COPY --chown=app:app LICENSE THIRD_PARTY_NOTICES.md /licenses/" in dockerfile
    assert "apt-get install" not in dockerfile
    assert "curl" not in dockerfile
