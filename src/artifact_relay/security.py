"""Secret comparison, password verification, session cookie signing, redirect safety."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def verify_bearer_token(header: str | None, expected: str) -> bool:
    """Return ``True`` iff ``header`` is ``Bearer <expected>``.

    The scheme is compared normally (it is not a secret); the token itself is compared with
    :func:`hmac.compare_digest` so that a wrong token cannot be recovered byte-by-byte by
    timing the response.
    """
    if not header:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


_HASHER = PasswordHasher()
SESSION_SALT = "artifact-relay.session.v1"
SHARE_SESSION_SALT = "artifact-relay.share-session.v1"


def verify_view_password(password: str, encoded_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash.

    Argon2 verification is itself constant-time with respect to the digest, and the plaintext
    is never stored, logged or echoed anywhere.
    """
    try:
        return _HASHER.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class PasswordVerificationGate:
    """A global bound on how many Argon2 verifications may run at once.

    Argon2id is memory-hard on purpose: the library defaults allocate 64 MiB per call. The
    login route is a sync endpoint, so Starlette dispatches it onto the anyio worker thread
    pool — 40 threads by default — and forty simultaneous attempts would ask for ~2.5 GiB.

    A *per-client* limit cannot help here, because the attempts deliberately come from many
    addresses; the bound has to be process-wide. Admission is non-blocking: queueing the
    surplus would keep the connections (and eventually the thread pool) occupied, whereas
    shedding immediately keeps memory flat and tells the caller to come back.
    """

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.max_concurrent = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def available(self) -> int:
        return self.max_concurrent - self.in_flight

    @contextmanager
    def admit(self) -> Iterator[bool]:
        """Yield ``True`` when a slot was reserved, ``False`` when the gate is saturated.

        The slot is returned on the way out even if the body raises — and a caller that was
        refused never releases one it did not hold, which would raise past the bound.
        """
        acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._in_flight += 1
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    self._in_flight -= 1
                self._semaphore.release()


class SessionSigner:
    """Stateless signed-cookie sessions.

    The cookie holds no secret material — only a version marker — so even a full cookie
    disclosure reveals nothing beyond "someone was logged in". Expiry is enforced
    server-side from the signed timestamp, not from the browser's ``Max-Age``.
    """

    def __init__(self, secret_key: str, max_age_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=SESSION_SALT)
        self.max_age_seconds = max_age_seconds

    def issue(self) -> str:
        token: str = self._serializer.dumps({"v": 1, "sub": "viewer"})
        return token

    def verify(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            payload = self._serializer.loads(token, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired):
            return False
        return isinstance(payload, dict) and payload.get("sub") == "viewer"


class ShareSessionSigner:
    def __init__(self, secret_key: str, max_age_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=SHARE_SESSION_SALT)
        self.max_age_seconds = max_age_seconds

    def issue(self, share_id: str) -> str:
        token: str = self._serializer.dumps({"v": 1, "sub": "artifact-share", "share_id": share_id})
        return token

    def verify(self, token: str | None, share_id: str) -> bool:
        if not token:
            return False
        try:
            payload = self._serializer.loads(token, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("sub") == "artifact-share"
            and payload.get("share_id") == share_id
        )


def safe_next_path(value: str | None, fallback: str = "/") -> str:
    """Return ``value`` only if it is a local path; otherwise ``fallback``.

    Blocks scheme-relative (``//evil``), backslash-confused (``/\\evil``) and absolute
    (``https://evil``) redirect targets, plus anything carrying control characters.
    """
    if not value:
        return fallback
    if any(ch in value for ch in ("\r", "\n", "\t", "\x00")) or any(ord(c) < 0x20 for c in value):
        return fallback
    normalised = value.replace("\\", "/")
    if not normalised.startswith("/") or normalised.startswith("//"):
        return fallback
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return fallback
    return value
