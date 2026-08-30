"""Structured JSON logging with hard redaction.

Two independent layers:

1. **Nothing sensitive is logged in the first place.** The access log records method, path,
   status, duration and a request id — never headers, never cookies, never bodies.
2. **A formatter that scrubs anyway.** Every configured secret is replaced with ``***`` in
   the fully rendered line, tracebacks included, so a careless `logger.info(f"...{token}")`
   added later still cannot leak. Layer 1 is the design; layer 2 is the seatbelt.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from artifact_relay.config import Settings

REDACTED = "***"
RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)
# Field names that must never be serialised, whatever they contain.
FORBIDDEN_FIELDS = frozenset(
    {"authorization", "cookie", "cookies", "password", "token", "secret", "set-cookie"}
)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED or key.startswith("_"):
                continue
            if key.lower() in FORBIDDEN_FIELDS:
                payload[key] = REDACTED
                continue
            simple = isinstance(value, str | int | float | bool | None)
            payload[key] = value if simple else repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
        return self.redact(rendered)


def secrets_of(settings: Settings) -> list[str]:
    return [
        settings.artifact_api_token.get_secret_value(),
        settings.view_password_hash.get_secret_value(),
        settings.session_secret_key.get_secret_value(),
    ]


def build_handler(settings: Settings, stream: TextIO | None = None) -> logging.Handler:
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter(secrets_of(settings)))
    return handler


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(build_handler(settings))
    root.setLevel(settings.log_level.upper())

    # uvicorn's own access log would print the raw request line; ours carries the same
    # information in structured form, without the query string being re-echoed.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.error").handlers = []
    logging.getLogger("uvicorn.error").propagate = True
