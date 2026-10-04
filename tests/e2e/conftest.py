"""E2E surface fixtures: file-config repos over tmp JSON + FakeClock (task 6).

The database stack is gone: the surface copies the example config into
tmp_path, pins calc coordinates via ``FileSettingsRepo.save``, and builds
the app with file repos only (no users, no database).
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from muhideen.adapters.file_config import (
    FilePlaylistRepo,
    FilePrayerRepo,
    FileSettingsRepo,
    load_config_file,
)
from muhideen.adapters.sse_bus import SSEBus
from muhideen.api.app import AppDeps, create_app

pytestmark = pytest.mark.e2e

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
EXAMPLE = (
    Path(__file__).resolve().parent.parent.parent / "config" / "muhideen.example.json"
)


class FakeClock:
    """File-local pinned clock; advance by calling advance(seconds)."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono = self._mono + seconds


@pytest.fixture
def surface(tmp_path: Path) -> SimpleNamespace:
    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    cfg = load_config_file(config_path)
    clock = FakeClock(PINNED_START)
    settings_repo = FileSettingsRepo(config_path)
    prayer_repo = FilePrayerRepo(
        tmp_path / "prayer_buffer.json", cfg.schedule.manual_days
    )
    playlist_repo = FilePlaylistRepo(config_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    bus = SSEBus()
    deps = AppDeps(
        settings_repo=settings_repo,
        prayer_repo=prayer_repo,
        clock=clock,
        event_bus=bus,
        playlist_repo=playlist_repo,
        media_dir=media_dir,
        config_path=config_path,
    )
    app = create_app(deps)
    return SimpleNamespace(
        app=app,
        deps=deps,
        clock=clock,
        bus=bus,
        settings_repo=settings_repo,
        prayer_repo=prayer_repo,
        playlist_repo=playlist_repo,
        config_path=config_path,
        media_dir=media_dir,
    )


@pytest.fixture
def client(surface: SimpleNamespace) -> Iterator[TestClient]:
    with TestClient(surface.app) as test_client:
        yield test_client


@pytest.fixture
def live_server(surface: SimpleNamespace) -> Iterator[str]:
    """Boot the app under real uvicorn on an ephemeral loopback port.

    Needed for what TestClient/ASGITransport cannot express: genuine TCP
    disconnects and many-concurrency SSE streams against a live server.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(256)
    port = sock.getsockname()[1]
    config = uvicorn.Config(surface.app, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name="e2e-uvicorn",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn failed to start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        if thread.is_alive():
            # a test failure can leave streams open; force, don't hang
            server.force_exit = True
            thread.join(timeout=5.0)
        sock.close()
