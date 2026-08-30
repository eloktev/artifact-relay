# syntax=docker/dockerfile:1.7

# Readable tags are retained while multi-arch manifest digests make builds reproducible.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.0@sha256:bc574e793452103839d769a20249cfe4c8b6e40e5c29fda34ceee26120eabe3b /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS runtime

LABEL org.opencontainers.image.title="Artifact Relay" \
      org.opencontainers.image.description="Self-hosted publisher for Markdown/HTML artifacts" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home /home/app --create-home app \
 && mkdir -p /data \
 && chown -R app:app /data

COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app LICENSE THIRD_PARTY_NOTICES.md /licenses/

WORKDIR /srv
USER app:app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"]

# Trust only FORWARDED_ALLOW_IPS (uvicorn defaults to loopback); never blanket-trust proxies.
CMD ["uvicorn", "artifact_relay.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", \
     "--no-access-log", "--workers", "1"]
