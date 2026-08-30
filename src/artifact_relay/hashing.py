"""Generate the Argon2id hash for ``VIEW_PASSWORD_HASH``.

Run interactively::

    python -m artifact_relay.hashing

The plaintext is read with :func:`getpass.getpass` (never echoed, never in shell history,
never written anywhere) and only the hash is printed. Argon2 defaults from `argon2-cffi`
are used deliberately — they track the OWASP recommendation, and the parameters are encoded
in the hash string, so raising them later does not invalidate existing hashes.
"""

from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher

HASHER = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return HASHER.hash(password)


def main() -> int:
    password = getpass.getpass("Пароль для просмотра: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"Пароль короче {MIN_PASSWORD_LENGTH} символов — выберите длиннее.",
            file=sys.stderr,
        )
        return 2
    if password != getpass.getpass("Повторите пароль: "):
        print("Пароли не совпадают.", file=sys.stderr)
        return 2
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
