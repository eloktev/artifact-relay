"""Artifact viewing (session authenticated), plus the login-safe public shell."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import Response as RawResponse

from artifact_relay.assets import ASSET_CSP, is_safe_asset_name, media_type_for
from artifact_relay.csp import ARTIFACT_SANDBOX, artifact_csp
from artifact_relay.dependencies import (
    get_embed_capability,
    get_settings,
    get_share_embed_capability,
    get_share_session_signer,
    get_store,
    has_session,
    require_session,
)
from artifact_relay.download import content_disposition, source_media_type
from artifact_relay.models import Artifact, ShareLink
from artifact_relay.ogimage import render_card
from artifact_relay.rendering import render_markdown
from artifact_relay.schemas import ShareRedeemRequest
from artifact_relay.templating import human_date, templates

router = APIRouter(tags=["viewer"], include_in_schema=False)

OG_DESCRIPTION_LIMIT = 200
SHARE_COOKIE_PREFIX = "artifact_share_"
SHARE_REDEEM_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "object-src 'none'",
    )
)


def load_artifact(request: Request, artifact_id: str) -> Artifact:
    """Fetch an artifact or raise the right HTTP status.

    404 for unknown ids, 410 for expired ones. Both are returned *before* any session check
    so that the answer does not depend on whether the caller is logged in — an anonymous
    prober learns nothing an authenticated one would not.
    """
    artifact = get_store(request).get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if artifact.is_expired(datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gone")
    return artifact


def og_context(request: Request, artifact: Artifact) -> dict[str, object]:
    settings = get_settings(request)
    summary = (artifact.summary or "").strip()
    if len(summary) > OG_DESCRIPTION_LIMIT:
        summary = summary[: OG_DESCRIPTION_LIMIT - 1].rstrip() + "…"
    return {
        "artifact": artifact,
        "og_title": artifact.title,
        "og_description": summary,
        "og_url": settings.absolute_url(f"/a/{artifact.id}"),
        "og_image": settings.absolute_url(f"/a/{artifact.id}/og.png"),
        "next": f"/a/{artifact.id}",
        "settings": settings,
    }


def share_cookie_name(share_id: str) -> str:
    return f"{SHARE_COOKIE_PREFIX}{share_id}"


def require_share_session(request: Request, share_id: str) -> ShareLink:
    share = get_store(request).get_share(share_id)
    if share is None or not share.is_active(datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    cookie = request.cookies.get(share_cookie_name(share.id))
    if not get_share_session_signer(request).verify(cookie, share.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return share


@router.get("/s/{share_id}", response_class=HTMLResponse)
def share_landing(request: Request, share_id: str) -> Response:
    share = get_store(request).get_share(share_id)
    if share is None or not share.is_active(datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    cookie = request.cookies.get(share_cookie_name(share.id))
    if get_share_session_signer(request).verify(cookie, share.id):
        artifact = load_artifact(request, share.artifact_id)
        if artifact.format == "html":
            token = get_share_embed_capability(request).issue(share.id, artifact.id)
            response = templates.TemplateResponse(
                request,
                "shared_artifact_html.html",
                {
                    "artifact": artifact,
                    "share": share,
                    "raw_url": f"/s/{share.id}/embed/{token}/",
                    "sandbox": ARTIFACT_SANDBOX,
                },
            )
            response.headers["Cache-Control"] = "private, no-store"
            return response
        if artifact.format == "markdown":
            source = get_store(request).read_source(artifact.id).decode("utf-8", errors="replace")
            rendered = render_markdown(source, asset_base=f"/s/{share.id}/assets/")
            response = templates.TemplateResponse(
                request,
                "shared_artifact.html",
                {
                    "artifact": artifact,
                    "share": share,
                    "body_html": rendered.html,
                    "toc": rendered.toc,
                    "has_mermaid": rendered.has_mermaid,
                },
            )
            response.headers["Cache-Control"] = "private, no-store"
            return response
    response = templates.TemplateResponse(request, "share_landing.html", {})
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = SHARE_REDEEM_CSP
    return response


@router.post("/s/{share_id}/redeem", status_code=status.HTTP_204_NO_CONTENT)
def redeem_share(request: Request, share_id: str, payload: ShareRedeemRequest) -> Response:
    share = get_store(request).authorize_share(share_id, payload.token)
    if share is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    settings = get_settings(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        share_cookie_name(share.id),
        get_share_session_signer(request).issue(share.id),
        max_age=settings.session_ttl_days * 86400,
        path=f"/s/{share.id}",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/s/{share_id}/assets/{name:path}")
def shared_asset(request: Request, share_id: str, name: str) -> Response:
    share = require_share_session(request, share_id)
    artifact = load_artifact(request, share.artifact_id)
    if not is_safe_asset_name(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    store = get_store(request)
    if store.get_asset(artifact.id, name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    root = (store.artifact_dir(artifact.id) / "assets").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    media_type, inline = media_type_for(name)
    disposition = "inline" if inline else "attachment"
    return RawResponse(
        content=path.read_bytes(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "Content-Security-Policy": ASSET_CSP,
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def require_shared_embed(request: Request, share_id: str, token: str) -> tuple[ShareLink, Artifact]:
    share = get_store(request).get_share(share_id)
    if share is None or not share.is_active(datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not get_share_embed_capability(request).verify(token, share.id, share.artifact_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    artifact = load_artifact(request, share.artifact_id)
    if artifact.format != "html":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return share, artifact


@router.get("/s/{share_id}/embed/{token}/", response_class=HTMLResponse)
def shared_embed_document(request: Request, share_id: str, token: str) -> Response:
    share, artifact = require_shared_embed(request, share_id, token)
    body = get_store(request).read_source(artifact.id)
    asset_prefix = get_settings(request).absolute_url(f"/s/{share.id}/embed/{token}/")
    return RawResponse(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": artifact_csp(asset_prefix),
            "X-Frame-Options": "SAMEORIGIN",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/s/{share_id}/embed/{token}/assets/{name:path}")
def shared_embed_asset(request: Request, share_id: str, token: str, name: str) -> Response:
    _, artifact = require_shared_embed(request, share_id, token)
    response = embed_asset_response(request, artifact, name)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/s/{share_id}/embed/{token}/{name}")
def shared_embed_asset_by_bare_name(
    request: Request, share_id: str, token: str, name: str
) -> Response:
    _, artifact = require_shared_embed(request, share_id, token)
    response = embed_asset_response(request, artifact, name)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    if not has_session(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    artifacts = get_store(request).list_live()
    topic_groups: dict[tuple[str, str], dict[str, object]] = {}
    for artifact in artifacts:
        topic_label = artifact.topic_name or (
            f"Топик {artifact.topic_id}" if artifact.topic_id else "Без топика"
        )
        chat_label = artifact.chat_name or artifact.platform or "Без источника"
        key = (chat_label, topic_label)
        group = topic_groups.setdefault(
            key,
            {"chat_name": chat_label, "topic_name": topic_label, "count": 0},
        )
        count = group["count"]
        if isinstance(count, int):
            group["count"] = count + 1

    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "artifacts": artifacts,
            "favorites": [artifact for artifact in artifacts if artifact.favorite],
            "topics": list(topic_groups.values()),
            "settings": get_settings(request),
        },
    )


@router.post("/a/{artifact_id}/favorite")
def toggle_favorite(request: Request, artifact_id: str) -> Response:
    require_session(request)
    load_artifact(request, artifact_id)
    if get_store(request).toggle_favorite(artifact_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return RedirectResponse(f"/a/{artifact_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/a/{artifact_id}/shares", response_class=HTMLResponse)
async def create_share_link(request: Request, artifact_id: str) -> Response:
    require_session(request)
    form = await request.form()
    expires_raw = form.get("expires_days")
    try:
        expires_days = int(expires_raw) if isinstance(expires_raw, str) else -1
    except ValueError:
        expires_days = -1
    artifact = load_artifact(request, artifact_id)
    if expires_days not in {0, 1, 7, 30}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    now = datetime.now(UTC)
    expires_at = None if expires_days == 0 else now + timedelta(days=expires_days)
    share, token = get_store(request).create_share(
        artifact.id, expires_at=expires_at, created_at=now
    )
    share_url = get_settings(request).absolute_url(f"/s/{share.id}") + f"#{token}"
    response = templates.TemplateResponse(
        request,
        "share_created.html",
        {
            "artifact": artifact,
            "share": share,
            "share_url": share_url,
            "expires_days": expires_days,
        },
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.post("/a/{artifact_id}/shares/{share_id}/revoke")
def revoke_share_link(request: Request, artifact_id: str, share_id: str) -> Response:
    require_session(request)
    artifact = load_artifact(request, artifact_id)
    share = get_store(request).get_share(share_id)
    if share is None or share.artifact_id != artifact.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    get_store(request).revoke_share(share.id)
    return RedirectResponse(f"/a/{artifact.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/a/{artifact_id}", response_class=HTMLResponse)
def view_artifact(request: Request, artifact_id: str) -> Response:
    artifact = load_artifact(request, artifact_id)

    if not has_session(request):
        # 200, not a redirect: Telegram's crawler must find the Open Graph tags at the
        # canonical artifact URL, and it does not follow a login redirect.
        return templates.TemplateResponse(
            request, "shell.html", og_context(request, artifact), status_code=status.HTTP_200_OK
        )

    store = get_store(request)
    context = og_context(request, artifact)
    now = datetime.now(UTC)
    context["active_shares"] = [
        share for share in store.list_shares(artifact.id) if share.is_active(now)
    ]
    source = store.read_source(artifact.id).decode("utf-8", errors="replace")

    if artifact.format == "html":
        # The body is never inlined into the viewer page; it is loaded into a sandboxed
        # iframe from a separate URL so it gets its own opaque origin. That opaque origin
        # cannot send the session cookie, so the iframe subtree is addressed by a freshly
        # minted, expiring, artifact-bound capability path instead.
        context["raw_url"] = get_embed_capability(request).path_for(artifact.id)
        context["sandbox"] = ARTIFACT_SANDBOX
        return templates.TemplateResponse(request, "artifact_html.html", context)

    # Artifact-relative references (`chart.png`, `assets/chart.png`) resolve against this.
    rendered = render_markdown(source, asset_base=f"/a/{artifact.id}/assets/")
    context["body_html"] = rendered.html
    context["toc"] = rendered.toc
    context["has_mermaid"] = rendered.has_mermaid
    return templates.TemplateResponse(request, "artifact.html", context)


def require_capability(request: Request, artifact_id: str, token: str) -> Artifact:
    """Authorise an iframe-subtree request from the capability token alone.

    No cookie is consulted: the caller is an opaque origin and cannot send one. The artifact
    is still loaded through the normal path afterwards, so deletion and expiry revoke a live
    capability immediately (404 / 410) without any token bookkeeping.
    """
    if not get_embed_capability(request).verify(token, artifact_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    artifact = load_artifact(request, artifact_id)
    if artifact.format != "html":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return artifact


def embed_asset_response(request: Request, artifact: Artifact, name: str) -> Response:
    """Serve one attachment into the sandboxed document.

    Same three gates as the session-authenticated asset route — name allowlist, metadata
    lookup, resolved-path containment — but the resource policy has to be ``cross-origin``:
    the requesting document has an opaque origin, which is same-origin with nothing, so
    ``same-origin`` would make the browser drop a response we deliberately authorised.
    Access control here is the capability in the path, not CORP, which is an embedding
    control rather than an access control.
    """
    if not is_safe_asset_name(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    store = get_store(request)
    if store.get_asset(artifact.id, name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    root = (store.artifact_dir(artifact.id) / "assets").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    media_type, inline = media_type_for(name)
    disposition = "inline" if inline else "attachment"
    return RawResponse(
        content=path.read_bytes(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "Content-Security-Policy": ASSET_CSP,
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/embed/{artifact_id}/{token}/", response_class=HTMLResponse)
def embed_document(request: Request, artifact_id: str, token: str) -> Response:
    """The standalone HTML artifact itself, for the sandboxed iframe.

    The trailing slash is load-bearing: it makes the capability path a *directory*, so a
    relative ``assets/chart.png`` written inside the artifact resolves to
    ``/embed/<id>/<token>/assets/chart.png`` and stays inside the capability.
    """
    artifact = require_capability(request, artifact_id, token)

    settings = get_settings(request)
    body = get_store(request).read_source(artifact.id)
    # One prefix covers both spellings the artifact may use, `assets/x` and bare `x`.
    asset_prefix = settings.absolute_url(f"/embed/{artifact.id}/{token}/")
    return RawResponse(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": artifact_csp(asset_prefix),
            "X-Frame-Options": "SAMEORIGIN",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/embed/{artifact_id}/{token}/assets/{name:path}")
def embed_asset(request: Request, artifact_id: str, token: str, name: str) -> Response:
    artifact = require_capability(request, artifact_id, token)
    return embed_asset_response(request, artifact, name)


@router.get("/embed/{artifact_id}/{token}/{name}")
def embed_asset_by_bare_name(request: Request, artifact_id: str, token: str, name: str) -> Response:
    """`<img src="chart.png">` next to the document, the same spelling Markdown accepts.

    Registered after the ``assets/`` route, so that prefix always wins.
    """
    artifact = require_capability(request, artifact_id, token)
    return embed_asset_response(request, artifact, name)


@router.get("/a/{artifact_id}/assets/{name:path}")
def view_asset(request: Request, artifact_id: str, name: str) -> Response:
    """Serve an artifact attachment.

    Three independent gates, any one of which is sufficient: the name allowlist, the
    metadata lookup (only registered assets exist), and a final containment check on the
    resolved filesystem path.
    """
    require_session(request)
    artifact = load_artifact(request, artifact_id)

    if not is_safe_asset_name(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    store = get_store(request)
    if store.get_asset(artifact.id, name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    root = (store.artifact_dir(artifact.id) / "assets").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    media_type, inline = media_type_for(name)
    disposition = "inline" if inline else "attachment"
    return RawResponse(
        content=path.read_bytes(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "Content-Security-Policy": ASSET_CSP,
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/a/{artifact_id}/source")
def download_source(request: Request, artifact_id: str) -> Response:
    """The artifact exactly as published.

    Never served as `text/html`: an HTML artifact's own source rendered inline on this
    origin would be a same-origin XSS, so it is always an attachment.
    """
    require_session(request)
    artifact = load_artifact(request, artifact_id)
    body = get_store(request).read_source(artifact.id)
    return RawResponse(
        content=body,
        media_type=source_media_type(artifact.format),
        headers={
            "Content-Disposition": content_disposition(artifact),
            "Content-Security-Policy": ASSET_CSP,
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/a/{artifact_id}/og.png")
def og_image(request: Request, artifact_id: str) -> Response:
    """The link-preview card.

    Intentionally unauthenticated: Telegram fetches it with no cookies. It therefore may
    only contain what the page's public meta tags already contain.
    """
    artifact = load_artifact(request, artifact_id)
    png = render_card(
        title=artifact.title,
        kind=artifact.format,
        created=human_date(artifact.created_at),
    )
    return RawResponse(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Content-Security-Policy": ASSET_CSP,
        },
    )
