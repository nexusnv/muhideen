"""Display route from file config + per-display language (task 4).

CWD-independent: example config located via Path(__file__) and copied
into tmp_path; file-backed AppDeps with a pinned FakeClock so the
calc schedule resolves deterministically.
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

    def now(self) -> datetime:
        return self._now


def _copy_with_coords(tmp_path: Path) -> Path:
    """Copy example config, pin calc coords so the display resolves."""
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    return dest


@pytest.fixture
def file_client(tmp_path: Path) -> Any:
    """File-backed app: FileSettings/Prayer/Playlist repos + FakeClock."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app

    dest = _copy_with_coords(tmp_path)
    cfg = load_config_file(dest)
    clock = FakeClock(PINNED_START)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        user_repo=None,
        clock=clock,  # type: ignore[arg-type]
        event_bus=SSEBus(),
        database=None,
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        yield client


def test_en_display_renders_global_palette_and_lang(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "main-hall"}).text
    assert "palette-classic-green" in html
    assert 'lang="en"' in html
    assert "Fajr" in html


def test_ms_display_renders_midnight_and_malay(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "entrance"}).text
    assert "palette-midnight" in html
    assert 'lang="ms"' in html
    assert "Zohor" in html
    assert "Subuh" in html


def test_unknown_id_falls_back_to_global(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "nope"}).text
    assert "palette-classic-green" in html
    assert 'lang="en"' in html


def test_custom_colors_style_only_when_set(file_client: TestClient) -> None:
    en_html = file_client.get("/display", params={"id": "main-hall"}).text
    assert "<style>:root" not in en_html
    ms_html = file_client.get("/display", params={"id": "entrance"}).text
    assert "<style>:root" in ms_html
    assert "#0b0f0e" in ms_html
