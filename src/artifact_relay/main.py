"""ASGI entrypoint: ``uvicorn artifact_relay.main:app``.

Settings are read from the environment at import time, so a missing or malformed secret
crashes the container immediately and visibly instead of surfacing as a 500 on the first
real request.
"""

from __future__ import annotations

from fastapi import FastAPI

from artifact_relay.app import create_app
from artifact_relay.config import Settings
from artifact_relay.logging_setup import configure_logging


def build() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]  # values come from the environment
    configure_logging(settings)
    return create_app(settings)


app = build()
