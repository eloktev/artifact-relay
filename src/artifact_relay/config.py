"""Runtime configuration.

Every secret is wrapped in :class:`pydantic.SecretStr` so that an accidental ``repr()`` in a
log line, traceback or error page prints ``**********`` instead of the real value.
"""

from __future__ import annotations

from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from argon2 import Type, extract_parameters
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MEGABYTE = 1024 * 1024
ARTIFACT_API_TOKEN_PLACEHOLDER = "replace-me-with-at-least-16-random-characters"  # noqa: S105 - intentionally rejected
SESSION_SECRET_KEY_PLACEHOLDER = "replace-me-with-at-least-32-random-characters"  # noqa: S105 - intentionally rejected


class Settings(BaseSettings):
    """Settings loaded from the process environment (or an ``.env`` file in development)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- required secrets -------------------------------------------------
    artifact_api_token: SecretStr
    view_password_hash: SecretStr
    session_secret_key: SecretStr

    # --- deployment -------------------------------------------------------
    data_dir: Path = Path("/data")
    base_url: str = "http://localhost:8000"
    share_links_enabled: bool = False
    log_level: str = "INFO"

    # --- lifecycle --------------------------------------------------------
    default_ttl_days: int = Field(default=30, ge=0)
    max_ttl_days: int = Field(default=3650, ge=1)
    session_ttl_days: int = Field(default=30, ge=1)
    janitor_interval_seconds: int = Field(default=3600, ge=60)

    # --- payload limits ---------------------------------------------------
    max_title_chars: int = Field(default=200, ge=1)
    max_summary_chars: int = Field(default=600, ge=1)
    max_content_bytes: int = Field(default=5 * MEGABYTE, ge=1)
    max_assets: int = Field(default=20, ge=0)
    max_asset_bytes: int = Field(default=20 * MEGABYTE, ge=1)

    # --- login throttling -------------------------------------------------
    login_max_attempts: int = Field(default=10, ge=1)
    login_window_seconds: int = Field(default=900, ge=1)
    # Process-wide ceiling on simultaneous Argon2id verifications. Each one costs ~64 MiB at
    # library-default parameters, so this is a memory bound, not a fairness knob. Attempts
    # beyond it are shed with 503 rather than queued.
    login_max_concurrent_verifications: int = Field(default=4, ge=1)

    # --- sandboxed iframe capability --------------------------------------
    # How long a minted /embed/<id>/<token>/ path stays valid. It is issued fresh on every
    # render, so this only has to outlive one viewing session of one document.
    embed_token_ttl_seconds: int = Field(default=3600, ge=60)

    # --- cookie -----------------------------------------------------------
    session_cookie_name: str = "ap_session"
    cookie_secure: bool = True

    @field_validator("base_url")
    @classmethod
    def _require_canonical_origin(cls, value: str) -> str:
        value = value.rstrip("/")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("BASE_URL must be a valid absolute HTTP(S) origin") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("BASE_URL must be a valid absolute HTTP(S) origin without a path")
        # Accessing the parsed port above rejects malformed/out-of-range ports. Retain it here
        # only to make that validation explicit to type checkers and future readers.
        _ = port
        return value

    @field_validator("view_password_hash")
    @classmethod
    def _require_argon2id_hash(cls, value: SecretStr) -> SecretStr:
        try:
            parameters = extract_parameters(value.get_secret_value())
        except ValueError as exc:
            raise ValueError("VIEW_PASSWORD_HASH must be a valid Argon2id encoded hash") from exc
        if parameters.type is not Type.ID:
            raise ValueError("VIEW_PASSWORD_HASH must be a valid Argon2id encoded hash")
        return value

    @field_validator("session_secret_key")
    @classmethod
    def _require_strong_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret == SESSION_SECRET_KEY_PLACEHOLDER:
            raise ValueError("SESSION_SECRET_KEY must not use the documented placeholder")
        if len(secret) < 32:
            raise ValueError("SESSION_SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("artifact_api_token")
    @classmethod
    def _require_strong_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if token == ARTIFACT_API_TOKEN_PLACEHOLDER:
            raise ValueError("ARTIFACT_API_TOKEN must not use the documented placeholder")
        if len(token) < 16:
            raise ValueError("ARTIFACT_API_TOKEN must be at least 16 characters")
        return value

    @model_validator(mode="after")
    def _require_secure_deployment_origin(self) -> Self:
        parsed = urlsplit(self.base_url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname or ""
        if self.share_links_enabled and scheme != "https":
            raise ValueError("SHARE_LINKS_ENABLED=true requires an HTTPS BASE_URL")
        is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            with suppress(ValueError):
                is_loopback = ip_address(hostname).is_loopback
        if scheme == "http" and not is_loopback:
            raise ValueError("BASE_URL must use HTTPS unless it addresses loopback")
        if scheme == "https" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when BASE_URL uses HTTPS")
        return self

    def absolute_url(self, path: str) -> str:
        """Join ``path`` onto the configured public base URL."""
        return f"{self.base_url}/{path.lstrip('/')}"
