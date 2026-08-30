#!/usr/bin/env python3
"""Validate immutable image input and tenant env before managed Compose."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: validate_managed_deployment.py DIGEST TENANT_ENV")
    digest, tenant_env_arg = sys.argv[1:]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise SystemExit(
            "ARTIFACT_RELAY_DIGEST must be exactly 64 lowercase hexadecimal characters"
        )

    tenant_env = Path(tenant_env_arg)
    if not tenant_env.is_file():
        raise SystemExit(f"tenant env does not exist: {tenant_env}")
    if tenant_env.stat().st_mode & 0o077:
        raise SystemExit("tenant env must not be accessible by group or other users")


if __name__ == "__main__":
    main()
