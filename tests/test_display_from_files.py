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
        clock=clock,  # type: ignore[arg-type]
        event_bus=SSEBus(),
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


def _row_segment(html: str, row_id: str) -> str:
    """HTML slice belonging to one prayer row (up to the next row/bound)."""
    parts = html.split(f'id="{row_id}"')
    assert len(parts) == 2, f"expected exactly one element with {row_id}"
    tail = parts[1].split('id="row-')[0].split('id="bound-')[0]
    return tail


def test_ms_display_renders_midnight_and_malay(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "entrance"}).text
    assert "palette-midnight" in html
    assert 'lang="ms"' in html
    fajr_row = _row_segment(html, "row-fajr")
    assert "Subuh" in fajr_row
    assert "Zohor" not in fajr_row
    dhuhr_row = _row_segment(html, "row-dhuhr")
    assert "Zohor" in dhuhr_row
    assert "Subuh" not in dhuhr_row


def test_unknown_id_falls_back_to_global(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "nope"}).text
    assert "palette-classic-green" in html
    assert 'lang="en"' in html


def test_no_per_display_style_override(file_client: TestClient) -> None:
    """Colors come only from the selected palette: no per-display <style>.

    The template emits no inline token rebind; each display renders its
    palette block (global or per-display ``theme.palette`` overlay).
    The assertion targets the rebind shape (``<style>body``), not any
    ``<style>`` tag, so future legitimate inline styles stay allowed.
    """
    en_html = file_client.get("/display", params={"id": "main-hall"}).text
    ms_html = file_client.get("/display", params={"id": "entrance"}).text
    assert "<style>body" not in en_html
    assert "<style>body" not in ms_html
    assert "--custom-" not in en_html
    assert "--custom-" not in ms_html


def test_per_display_palette_overlay_selects_full_token_set(
    tmp_path: Path,
) -> None:
    """Operator picks a palette, not colors: sand overlay renders sand."""
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
    # Palette-only override (no per-color keys): the whole vetted token
    # set follows, so dividers/tints cannot mix across palettes.
    raw["displays"]["entrance"]["theme"]["palette"] = "sand"
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    cfg = load_config_file(dest)
    clock = FakeClock(PINNED_START)
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
        html = client.get("/display", params={"id": "entrance"}).text
        assert "palette-sand" in html
        assert "<style>body" not in html


def test_display_silent_without_adhan_file(tmp_path: Path) -> None:
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
    # Audio enabled but the configured file absent: the display must stay
    # silent (no element, still 200) even inside the ADHAN window.
    raw["adhan_audio"]["enabled"] = True
    raw["adhan_audio"]["file"] = "missing-adhan.mp3"
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    cfg = load_config_file(dest)
    zone = FileSettingsRepo(dest).load().zone
    clock = FakeClock(PINNED_START)
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
        day = client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": zone}
        ).json()
        hour, minute = (int(part) for part in day["prayers"]["dhuhr"].split(":"))
        # The audio element only renders inside the ADHAN window, so pin
        # the clock one minute past dhuhr instead of PINNED_START (NORMAL).
        clock._now = datetime(2025, 10, 20, hour, minute, tzinfo=KL) + timedelta(
            minutes=1
        )
        response = client.get("/display", params={"id": "main-hall"})
        assert response.status_code == 200
        assert 'id="adhan-audio"' not in response.text


def test_display_serves_configured_adhan_file(tmp_path: Path) -> None:
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
    raw["adhan_audio"]["enabled"] = True
    raw["adhan_audio"]["file"] = "custom/adhan2.mp3"
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    media = tmp_path / "media"
    (media / "custom").mkdir(parents=True)
    (media / "custom" / "adhan2.mp3").write_bytes(
        b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
    )
    cfg = load_config_file(dest)
    zone = FileSettingsRepo(dest).load().zone
    clock = FakeClock(PINNED_START)
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
        day = client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": zone}
        ).json()
        hour, minute = (int(part) for part in day["prayers"]["dhuhr"].split(":"))
        # The audio element only renders inside the ADHAN window, so pin
        # the clock one minute past dhuhr instead of PINNED_START (NORMAL).
        clock._now = datetime(2025, 10, 20, hour, minute, tzinfo=KL) + timedelta(
            minutes=1
        )
        html = client.get("/display", params={"id": "main-hall"}).text
        assert 'id="adhan-audio"' in html
        assert 'src="/media/custom/adhan2.mp3"' in html
        audio = client.get("/media/custom/adhan2.mp3")
        assert audio.status_code == 200


def test_countdown_format_unified_across_styles(tmp_path: Path) -> None:
    """countdown_style is accepted for compat; the screen renders the single
    design-locked HH:MM:SS format either way (no boxes/inline fork)."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app

    bodies: list[str] = []
    for style in ("boxes", "inline"):
        dest = tmp_path / f"muhideen-{style}.json"
        shutil.copy(EXAMPLE, dest)
        raw = json.loads(dest.read_text())
        raw["schedule"]["lat"] = 3.07
        raw["schedule"]["lon"] = 101.69
        raw["theme"]["countdown_style"] = style
        dest.write_text(json.dumps(raw, indent=2) + "\n")
        cfg = load_config_file(dest)
        zone = FileSettingsRepo(dest).load().zone
        media = tmp_path / f"media-{style}"
        media.mkdir(exist_ok=True)
        clock = FakeClock(PINNED_START)
        deps = AppDeps(
            settings_repo=FileSettingsRepo(dest),
            prayer_repo=FilePrayerRepo(
                tmp_path / f"buffer-{style}.json", cfg.schedule.manual_days
            ),
            clock=clock,  # type: ignore[arg-type]
            event_bus=SSEBus(),
            playlist_repo=FilePlaylistRepo(dest),
            media_dir=media,
            config_path=dest,
        )
        app = create_app(deps)
        with TestClient(app) as client:
            day = client.get(
                "/api/prayer-day", params={"date": "2025-10-20", "zone": zone}
            ).json()
            hour, minute = (int(part) for part in day["prayers"]["fajr"].split(":"))
            # Pin 3 minutes before fajr (inside the 10-min fajr countdown
            # override) so the state is PRE_ADHAN and the countdown renders.
            clock._now = datetime(2025, 10, 20, hour, minute, tzinfo=KL) - timedelta(
                minutes=3
            )
            response = client.get("/display", params={"id": "main-hall"})
            assert response.status_code == 200
            bodies.append(response.text)
    # PRE_ADHAN renders the block, so `== 1` holds for both styles; the
    # absence check proves there is no boxes/inline fork.
    assert (
        bodies[0].count('class="countdown"')
        == bodies[1].count('class="countdown"')
        == 1
    )
    assert "countdown-inline" not in bodies[0] and "countdown-inline" not in bodies[1]


def test_no_coords_missing_timetable_guides_operator(tmp_path: Path) -> None:
    """No-coords + empty timetable: the 404 slate carries guidance (#86).

    The example config ships ``lat``/``lon`` null and the tmp buffer has
    no synced rows, so nothing resolves for the pinned date: the slate
    must tell the operator to set coordinates in ``muhideen.json`` or
    sync the zone timetable (status stays 404 — no contract change).
    """
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
    assert raw["schedule"]["lat"] is None
    assert raw["schedule"]["lon"] is None
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    cfg = load_config_file(dest)
    clock = FakeClock(PINNED_START)
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
        response = client.get("/display", params={"id": "main-hall"})
        assert response.status_code == 404
        assert "No schedule" in response.text
        assert "coordinates" in response.text
        assert "muhideen.json" in response.text
        assert "sync" in response.text.lower()


def test_coords_present_missing_timetable_keeps_bare_slate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ScheduleError with coords set: bare slate, no guidance copy (#86).

    Coordinates are present so the operator needs no coordinate guidance;
    the failure is timetable-only, so the slate keeps the bare message.
    The error is forced via the engine (calc would resolve with coords).
    """
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app
    from muhideen.core.errors import ScheduleError
    from muhideen.engine import Engine

    def _boom(*args: object, **kwargs: object) -> object:
        raise ScheduleError("no schedule", zone="SGR01", date="2025-10-20")

    monkeypatch.setattr(Engine, "resolve_day", _boom)
    dest = _copy_with_coords(tmp_path)
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
        response = client.get("/display", params={"id": "main-hall"})
        assert response.status_code == 404
        assert "No schedule" in response.text
        assert "coordinates" not in response.text
        assert "muhideen.json" not in response.text


def test_per_display_dim_override_reselects_state_at_boundary(
    tmp_path: Path,
) -> None:
    """The display pin reselects SALAH_DIM, not just dim_until (#93).

    Full manual pin (dhuhr 13:15, 10m iqamah delay → iqamah 13:25) with the
    global 20m dim ending at 13:45: at 13:50 the global render is NORMAL,
    but ``entrance`` (30m pin, dim until 13:55) must render SALAH_DIM.
    The old post-hoc patch kept the global NORMAL state with a future
    dim_until — an inconsistent pair.
    """
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
    raw["schedule"]["manual_days"] = [
        {
            "date": "2025-10-20",
            "imsak": "05:48",
            "fajr": "05:58",
            "syuruq": "07:05",
            "dhuha": "07:33",
            "dhuhr": "13:15",
            "asr": "16:30",
            "maghrib": "19:15",
            "isha": "20:30",
        }
    ]
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    cfg = load_config_file(dest)
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    clock = FakeClock(datetime(2025, 10, 20, 13, 50, tzinfo=KL))
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
        pinned = client.get("/display", params={"id": "entrance"})
        assert pinned.status_code == 200
        assert "state-salah_dim" in pinned.text
        assert 'data-state="SALAH_DIM"' in pinned.text
        assert 'id="dim-skip"' in pinned.text
        worldwide = client.get("/display", params={"id": "main-hall"})
        assert worldwide.status_code == 200
        assert "state-salah_dim" not in worldwide.text
        assert 'data-state="NORMAL"' in worldwide.text
