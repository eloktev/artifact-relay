"""Human-readable HTML error pages for the viewer, JSON for the API."""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from artifact_relay.templating import templates

PAGES: dict[int, tuple[str, str]] = {
    403: ("Нужен вход", "Эта страница доступна только после входа с паролем."),
    404: ("Не найдено", "Такого материала нет. Возможно, ссылка устарела или неверна."),
    410: ("Срок хранения истёк", "Материал был опубликован временно и уже удалён."),
    413: ("Слишком большой запрос", "Материал превышает допустимый размер."),
    429: ("Слишком много попыток", "Подождите немного и попробуйте снова."),
    500: ("Внутренняя ошибка", "Что-то пошло не так. Попробуйте позже."),
    503: ("Сервис занят", "Сейчас слишком много запросов. Повторите через секунду."),
}
FALLBACK = ("Ошибка", "Запрос не может быть выполнен.")


# Routes a *program* fetches rather than a person navigates to: the publisher API, the
# capability subtree inside the sandboxed iframe, and an artifact's own sub-resources.
# Handing an <img> or a `curl -o` a full HTML error document is noise at best — and inside
# the iframe the document arrives under a CSP that forbids rendering it at all.
MACHINE_ROUTES = re.compile(
    r"^/api/"
    r"|^/embed/"
    r"|^/a/[^/]+/(?:assets/|source$|og\.png$)"
)


def wants_html(request: Request) -> bool:
    """A rendered page for pages; JSON for sub-resources.

    Deliberately not based on the Accept header: Telegram's in-app browser and several
    crawlers send `Accept: */*`, and they would otherwise be handed raw JSON for the
    artifact page itself — which is the one response that has to stay HTML for them.
    """
    return not MACHINE_ROUTES.search(request.url.path)


def register(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _handler(request: Request, exc: StarletteHTTPException) -> Response:
        if not wants_html(request):
            return await http_exception_handler(request, exc)
        heading, explanation = PAGES.get(exc.status_code, FALLBACK)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"heading": heading, "explanation": explanation},
            status_code=exc.status_code,
            headers=dict(exc.headers or {}),
        )
