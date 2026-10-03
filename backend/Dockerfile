# syntax=docker/dockerfile:1

# ───────────── stage 1: dependencies (uv) ─────────────
# Only pyproject.toml + uv.lock are copied here, so this (slow) layer is rebuilt
# only when the dependencies change, not on every code edit.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# ───────────── stage 2: runtime ─────────────
FROM python:3.14-slim

# an unprivileged user; /data holds everything that must survive a restart
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/qdrant /data/media /data/sync \
    && chown -R app:app /data

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .

# These paths are read by core/settings.py. Mount a persistent volume on /data:
#   /data/qdrant   embedded Qdrant (the vector database)
#   /data/usage.db visitors' question / chat counters (SQLite)
#   /data/media    photos served at /media
#   /data/sync     state of the notes synchronisation job
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QDRANT_STORAGE_PATH=/data/qdrant \
    USAGE_DB_PATH=/data/usage.db \
    MEDIA_DIR=/data/media

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"]

# ONE worker on purpose: the embedded Qdrant allows a single client per storage path, and the
# agent's conversation memory lives in the process. To scale out, move both to network services.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
