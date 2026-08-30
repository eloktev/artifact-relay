import subprocess
import sys

ENV = {
    "ARTIFACT_API_TOKEN": "env-token-abcdefghijklmnop",
    "VIEW_PASSWORD_HASH": "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$0000000000000000000000",
    "SESSION_SECRET_KEY": "e" * 48,
    "BASE_URL": "https://artifacts.example.test/",
    "DEFAULT_TTL_DAYS": "7",
}


def test_the_asgi_entrypoint_builds_an_app_from_the_environment(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    import importlib

    from artifact_relay import main

    importlib.reload(main)

    assert main.app.title == "Artifact Relay"
    settings = main.app.state.settings
    assert settings.default_ttl_days == 7
    assert settings.base_url == "https://artifacts.example.test", "trailing slash must be trimmed"
    assert settings.data_dir == tmp_path / "data"


def test_missing_secrets_fail_fast_with_a_clear_message(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", "import artifact_relay.main"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "DATA_DIR": str(tmp_path), "HOME": str(tmp_path)},
        cwd=str(tmp_path),
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ARTIFACT_API_TOKEN" in combined.upper()


def test_password_hashing_helper_produces_a_verifiable_argon2id_hash():
    from artifact_relay.hashing import hash_password
    from artifact_relay.security import verify_view_password

    encoded = hash_password("пароль для просмотра")

    assert encoded.startswith("$argon2id$")
    assert verify_view_password("пароль для просмотра", encoded) is True
    assert verify_view_password("другой пароль", encoded) is False
    assert "пароль для просмотра" not in encoded


def test_password_hashes_are_salted_so_two_runs_differ():
    from artifact_relay.hashing import hash_password

    assert hash_password("одинаковый") != hash_password("одинаковый")
