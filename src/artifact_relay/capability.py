"""Signed, expiring, artifact-bound capability paths for the sandboxed HTML iframe.

A standalone HTML artifact is framed with ``sandbox="allow-scripts"`` and **without**
``allow-same-origin``, so the document runs in an *opaque origin*. That is the whole point —
it cannot touch ``document.cookie``, ``localStorage`` or anything else on this origin — but it
also means every subresource the document asks for is, from the browser's point of view, a
cross-site request from a null origin:

* the ``SameSite=Lax`` session cookie is not attached, so a session-gated asset route answers
  ``403`` no matter who is logged in;
* ``Cross-Origin-Resource-Policy: same-origin`` rejects the response as well, because an
  opaque origin is same-origin with nothing;
* ``'self'`` in a CSP matches nothing there, so allowances must name an absolute prefix.

So the iframe's subtree needs a credential that is *not* a cookie. This module mints one:
an :mod:`itsdangerous` signature over the artifact id, verified against the id in the URL.

Deliberate properties:

* **Artifact-bound.** The id is inside the signed payload and is compared with the id in the
  path, so a capability for one artifact opens nothing else.
* **Expiring.** Signed timestamps are checked server-side against ``EMBED_TOKEN_TTL_SECONDS``.
* **Not an identity.** It authorises exactly two shapes of GET — the document and its own
  assets — and nothing else in the service. It is not the session cookie and grants none of
  the session's powers (no source download, no other artifacts).
* **Revocable in the ways that matter.** Rotating ``SESSION_SECRET_KEY`` invalidates every
  outstanding capability, and deleting or expiring the artifact makes it moot immediately,
  because the routes still load the artifact through the normal 404/410 path.
* **Positional, not a query parameter.** The token occupies a *directory* segment above the
  document so that a relative ``assets/chart.png`` inside the artifact still resolves within
  the capability path. A query string would be dropped by relative resolution.

The token appears in a URL, so it is kept out of the access log (see
:func:`artifact_relay.middleware.redact_path`) and out of ``Referer`` (the service sends
``Referrer-Policy: no-referrer`` on every response).
"""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

EMBED_SALT = "artifact-relay.embed.v1"
SHARE_EMBED_SALT = "artifact-relay.share-embed.v1"
EMBED_PREFIX = "/embed"


class EmbedCapability:
    """Mints and checks the capability token for one running service."""

    def __init__(self, secret_key: str, ttl_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=EMBED_SALT)
        self.ttl_seconds = ttl_seconds

    def issue(self, artifact_id: str) -> str:
        token: str = self._serializer.dumps({"v": 1, "aid": artifact_id})
        return token

    def verify(self, token: str, artifact_id: str) -> bool:
        """``True`` only for an unexpired signature naming exactly ``artifact_id``."""
        if not token:
            return False
        try:
            payload = self._serializer.loads(token, max_age=self.ttl_seconds)
        except (BadSignature, SignatureExpired):
            return False
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return False
        claimed = payload.get("aid")
        if not isinstance(claimed, str):
            return False
        return hmac.compare_digest(claimed.encode("utf-8"), artifact_id.encode("utf-8"))

    def path_for(self, artifact_id: str) -> str:
        """The document URL. The trailing slash is load-bearing for relative assets."""
        return f"{EMBED_PREFIX}/{artifact_id}/{self.issue(artifact_id)}/"


class ShareEmbedCapability:
    """Mints iframe tokens bound to one share and one artifact."""

    def __init__(self, secret_key: str, ttl_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=SHARE_EMBED_SALT)
        self.ttl_seconds = ttl_seconds

    def issue(self, share_id: str, artifact_id: str) -> str:
        token: str = self._serializer.dumps({"v": 1, "sid": share_id, "aid": artifact_id})
        return token

    def verify(self, token: str, share_id: str, artifact_id: str) -> bool:
        """Accept only an unexpired token naming this exact share/artifact pair."""
        if not token:
            return False
        try:
            payload = self._serializer.loads(token, max_age=self.ttl_seconds)
        except (BadSignature, SignatureExpired):
            return False
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return False
        claimed_share = payload.get("sid")
        claimed_artifact = payload.get("aid")
        if not isinstance(claimed_share, str) or not isinstance(claimed_artifact, str):
            return False
        return hmac.compare_digest(
            claimed_share.encode("utf-8"), share_id.encode("utf-8")
        ) and hmac.compare_digest(claimed_artifact.encode("utf-8"), artifact_id.encode("utf-8"))
