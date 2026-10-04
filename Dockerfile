# Muhideen v1.0 application image (Linux + Docker + host-provisioned kiosk).
#
# Reproducible offline-capable build: dependencies resolve from the locked
# set (uv.lock) with --locked, so the image pins exactly what CI tested.
# Runtime state (file config + timetable cache + media) lives in mounted
# volumes, never in the image layers.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Locked dependency set first (layer cache: rebuilds only when deps change).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Application source (wheel build context is the sdist/wheel path; here we
# install the project itself so the `muhideen` entrypoint lands).
COPY src ./src
RUN uv sync --locked --no-dev

# Drop privileges (mirrors the old systemd `User=muhideen`): a compromise
# through the LAN-exposed API must not yield root inside the container or
# unrestricted access to the mounted volumes. The venv binary runs
# directly (no `uv run` at runtime, so no cache/lockfile writes as the
# unprivileged user). Bind mounts from compose provide /config and /media;
# the seed below only sets ownership for named-volume first mounts.
RUN useradd --system --create-home --home-dir /home/muhideen \
      --shell /usr/sbin/nologin muhideen \
  && mkdir -p /config /media \
  && chown -R muhideen:muhideen /app /config /media /home/muhideen
USER muhideen

# Runtime: single worker over HTTP; config mounted at /config, media at
# /media by compose. Edit the mounted muhideen.json — the watcher reloads
# it live (~1s), no login and no rebuild.
EXPOSE 8000
VOLUME ["/config", "/media"]
CMD ["/app/.venv/bin/muhideen", "--host", "0.0.0.0", "--port", "8000", "--config", "/config/muhideen.json", "--prayer-buffer", "/config/prayer_buffer.json", "--media-dir", "/media"]
