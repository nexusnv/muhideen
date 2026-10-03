# Muhideen v1.0 application image (Linux + Docker + host-provisioned kiosk).
#
# Reproducible offline-capable build: dependencies resolve from the locked
# set (uv.lock) with --locked, so the image pins exactly what CI tested.
# Runtime state (SQLite + uploads) lives in a mounted volume, never in
# the image layers.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Locked dependency set first (layer cache: rebuilds only when deps change).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Application source (wheel build context is the sdist/wheel path; here we
# install the project itself so `muhideen` / `muhideen-seed` entrypoints land).
COPY src ./src
RUN uv sync --locked --no-dev

# Runtime: single worker over HTTP; state mounted at /data by compose.
# Seed/configure via `muhideen-seed` (first boot) or the admin wizard.
EXPOSE 8000
VOLUME ["/data"]
CMD ["uv", "run", "--no-dev", "muhideen", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/muhideen.db"]
