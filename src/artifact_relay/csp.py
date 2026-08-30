"""Content-Security-Policy for standalone HTML artifacts.

The artifact document is framed with ``sandbox="allow-scripts"`` and therefore runs in an
**opaque origin**: ``'self'`` would match nothing there, so every allowance has to name the
artifact's own absolute asset prefix explicitly.

What this policy buys, concretely:

* ``connect-src 'none'`` — no ``fetch``/XHR/WebSocket/EventSource/``sendBeacon``.
* asset-prefix-only ``img-src``/``font-src``/``media-src`` — no pixel-beacon exfiltration to
  a third party; a subresource request can only ever reach this service.
* ``form-action 'none'`` — no form submission anywhere.
* ``frame-src``/``child-src``/``worker-src``/``object-src`` ``'none'`` — no nested contexts.
* ``sandbox allow-scripts`` repeated as a CSP directive, so the sandbox holds even if the
  document is ever fetched outside our iframe.
* No ``allow-same-origin``: the document cannot read ``document.cookie``, ``localStorage`` or
  anything else belonging to the service origin.

Residual risk, documented rather than hidden: a sandboxed document may still navigate
*itself* (``location = ...``). No CSP directive in current browsers prevents that (the
``navigate-to`` directive was removed from the spec). Publishing requires the bearer token,
so this only matters if the token itself is compromised. See README, "Security model".
"""

from __future__ import annotations

ARTIFACT_SANDBOX = "allow-scripts"


def artifact_csp(asset_prefix: str) -> str:
    """Policy for the standalone-HTML iframe document."""
    return "; ".join(
        (
            "default-src 'none'",
            "script-src 'unsafe-inline'",
            "style-src 'unsafe-inline'",
            f"img-src {asset_prefix} data:",
            f"font-src {asset_prefix} data:",
            f"media-src {asset_prefix}",
            "connect-src 'none'",
            "frame-src 'none'",
            "child-src 'none'",
            "worker-src 'none'",
            "manifest-src 'none'",
            "object-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self'",
            f"sandbox {ARTIFACT_SANDBOX}",
        )
    )
