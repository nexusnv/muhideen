"""Task 6: the deleted admin/database surface stays gone (404).

Every removed page and API route must answer 404 on the file-config app,
while the kept public surface (prayer-day, next-event, events, version,
display) keeps serving.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)


class FakeClock:
    """Pinned clock; mirrors tests/e2e/conftest.py (no shared import)."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono


@pytest.fixture
def file_client(tmp_path: Path) -> Any:
    """File-backed app over the example config (no database, no users)."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app

    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    cfg = load_config_file(dest)
    clock = FakeClock(PINNED_START)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=clock,  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/login",
        "/admin/setup",
        "/admin/settings",
        "/admin/playlists",
        "/api/settings",
        "/api/auth/session",
        # NOTE (admin Task 3): /api/playlists + /api/playlists/preview are
        # reintroduced here as public reads (spec §1/§3) — see
        # test_reintroduced_playlist_reads_serve below. The remaining
        # entries stay 404 until their tasks land (full split in Task 7).
        "/api/displays",
        "/api/logs",
    ],
)
def test_removed_surface_returns_404(file_client: TestClient, path: str) -> None:
    assert file_client.get(path).status_code == 404


def test_reintroduced_playlist_reads_serve(file_client: TestClient) -> None:
    """Task 3 reintroduces playlist reads as public (spec §1/§3)."""
    assert file_client.get("/api/playlists").status_code == 200
    preview = file_client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert preview.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/auth/login", {"password": "long-enough-password"}),
        ("post", "/api/auth/setup", {"password": "long-enough-password"}),
        ("post", "/api/auth/logout", {}),
        ("put", "/api/settings", {}),
        ("put", "/api/manual-day", {}),
        ("delete", "/api/manual-day", None),
        # NOTE (admin Task 3): POST /api/playlists is reintroduced as a
        # gated write — without a boot token this fixture answers 503
        # (see test_reintroduced_playlist_write_gated below), not 404.
        ("post", "/api/adhan-audio", {}),
        ("delete", "/api/adhan-audio", None),
        ("post", "/api/backup/export", {}),
        ("post", "/api/backup/restore", {}),
        ("patch", "/api/displays/HALL-01", {}),
        ("patch", "/api/display-groups/Default", {}),
    ],
)
def test_removed_mutations_return_404(
    file_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    query = {"date": "2025-10-20"} if path == "/api/manual-day" else None
    response = file_client.request(method, path, json=payload, params=query)
    assert response.status_code == 404


def test_reintroduced_playlist_write_gated(file_client: TestClient) -> None:
    """Task 3 reintroduces playlist writes as gated (503 with no boot token)."""
    assert file_client.post("/api/playlists", json={}).status_code == 503


def test_kept_public_surface_still_serves(file_client: TestClient) -> None:
    assert file_client.get("/api/version").status_code == 200
    day = file_client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert day.status_code == 200
    event = file_client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert event.status_code == 200
    display = file_client.get("/display", params={"id": "main-hall"})
    assert display.status_code == 200
