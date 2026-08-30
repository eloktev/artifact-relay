"""Viewer login / logout."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from artifact_relay.config import Settings
from artifact_relay.dependencies import (
    client_key,
    get_login_limiter,
    get_session_signer,
    get_settings,
    get_verification_gate,
)
from artifact_relay.security import safe_next_path, verify_view_password
from artifact_relay.templating import templates

router = APIRouter(tags=["auth"], include_in_schema=False)


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_days * 86400,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> Response:
    settings = get_settings(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": safe_next_path(next), "error": None, "settings": settings},
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
) -> Response:
    settings = get_settings(request)
    target = safe_next_path(next)
    limiter = get_login_limiter(request)
    key = client_key(request)

    retry_after = limiter.retry_after(key)
    if retry_after:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": target,
                "error": "Слишком много попыток. Попробуйте позже.",
                "settings": settings,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    # Bound the *global* number of in-flight Argon2 verifications before starting one. Each
    # costs ~64 MiB; the per-client throttle above cannot help when the flood is distributed.
    with get_verification_gate(request).admit() as admitted:
        if not admitted:
            # Shed, do not queue: holding the request would keep the memory reserved anyway.
            # This is not a failed attempt, so it deliberately does not feed the throttle.
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "next": target,
                    "error": "Сервис занят. Повторите попытку через секунду.",
                    "settings": settings,
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "1"},
            )
        password_is_correct = verify_view_password(
            password, settings.view_password_hash.get_secret_value()
        )

    if not password_is_correct:
        limiter.register_failure(key)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": target, "error": "Неверный пароль", "settings": settings},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    limiter.reset(key)
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, settings, get_session_signer(request).issue())
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    settings = get_settings(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response
