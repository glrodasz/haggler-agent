# syntax=docker/dockerfile:1
# Image for the daily Cloud Run Job ("haggler daily --store gcs").
# Built and pushed by .github/workflows/deploy-job.yml; see docs/SETUP.md.

FROM python:3.13-slim

# uv is copied from its official image (pinned) instead of pip-installed: no extra layer of
# build tooling, and the same resolver that produced uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

# - compile bytecode at build time: the container runs as a non-root user that cannot write
#   __pycache__ next to the sources, and Cloud Run Jobs cold-start on every execution;
# - copy (not hardlink) from the cache mount, which lives on another filesystem;
# - never download a Python: the base image's 3.13 is the one we ship.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, in their own layer: it is rebuilt only when the lockfile changes.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Project code and the data the job reads at runtime (catalog + curated YAML). README/LICENSE are
# needed by the build backend (package metadata).
COPY README.md LICENSE ./
COPY src/ src/
COPY catalog/ catalog/
COPY agents/ agents/
COPY data/curated/ data/curated/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Unprivileged runtime user with a writable home (google-auth/ADK may cache small files there).
RUN groupadd --gid 10001 haggler \
    && useradd --uid 10001 --gid haggler --create-home --home-dir /home/haggler \
       --shell /usr/sbin/nologin haggler

# HAGGLER_ROOT: where catalog/ and data/ live (config.repo_root). The local store (only used
# when debugging with "--store local") goes to /tmp, the only writable path on Cloud Run.
ENV HAGGLER_ROOT=/app \
    HAGGLER_LOCAL_DIR=/tmp/haggler \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

USER haggler

ENTRYPOINT ["/app/.venv/bin/haggler"]
CMD ["daily", "--store", "gcs"]
