"""Jinja2 environment shared by the server-rendered pages."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def human_date(value: datetime) -> str:
    local = value.astimezone(UTC)
    return f"{local.day} {MONTHS_RU[local.month - 1]} {local.year}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True
templates.env.filters["human_date"] = human_date
