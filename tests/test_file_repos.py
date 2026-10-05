"""File-backed repos over the core ports (task 2).

CWD-independent: example config is located via ``Path(__file__)`` and copied
into ``tmp_path``; no test imports from ``muhideen.api`` (adapters only).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from muhideen.adapters.file_config import (
    FilePlaylistRepo,
    FilePrayerRepo,
    FileSettingsRepo,
    load_config_file,
)
from muhideen.core.errors import ConfigError
from muhideen.core.values import MarkerName, PrayerDay, ScheduleSource

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

PINNED = date(2026, 4, 1)  # manual_days pin in the example config
ZONE = "SGR01"

_TIMES = {
    "imsak": time(5, 48),
    "fajr": time(5, 58),
    "syuruq": time(7, 5),
    "dhuha": time(7, 33),
    "dhuhr": time(13, 15),
    "asr": time(16, 30),
    "maghrib": time(19, 15),
    "isha": time(20, 30),
}

FETCHED = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)


def _copy_example(tmp_path: Path) -> Path:
    """Copy the example config into tmp_path; return the copy's path."""
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    return dest


def _jakim_day(day: date, zone: str = ZONE) -> PrayerDay:
    """One ordered JAKIM day for buffer tests (mirrors the example pin)."""
    return PrayerDay(
        date=day,
        zone=zone,
        source=ScheduleSource.JAKIM,
        fetched_at=FETCHED,
        **_TIMES,  # type: ignore[arg-type]
    )


def _settings_repo(tmp_path: Path) -> tuple[FileSettingsRepo, Path]:
    """Fresh repo over an example-config copy in tmp_path."""
    path = _copy_example(tmp_path)
    return FileSettingsRepo(path), path


def _prayer_repo(tmp_path: Path) -> tuple[FilePrayerRepo, Path]:
    """Fresh prayer repo: manual pins from the example copy, buffer in tmp."""
    config_path = _copy_example(tmp_path)
    cfg = load_config_file(config_path)
    buffer_path = tmp_path / "buffer.json"
    return FilePrayerRepo(buffer_path, cfg.schedule.manual_days), buffer_path


# Settings


def test_settings_loads_zone_and_theme(tmp_path: Path):
    repo, _ = _settings_repo(tmp_path)
    settings = repo.load()
    assert settings.zone == ZONE
    assert settings.masjid_name == "Masjid An-Nur"
    assert settings.timezone == "Asia/Kuala_Lumpur"
    assert settings.theme.palette == "classic-green"
    assert settings.theme.density == "comfortable"
    assert len(settings.iqamah_rules) == 6
    assert {rule.prayer for rule in settings.iqamah_rules} == {
        MarkerName.FAJR,
        MarkerName.DHUHR,
        MarkerName.ASR,
        MarkerName.MAGHRIB,
        MarkerName.ISHA,
        MarkerName.JUMUAH,
    }
    assert settings.countdown_before_adhan_overrides == {"fajr": 10}


def test_settings_save_round_trips_preserving_file_sections(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    before = json.loads(path.read_text())
    settings = replace(repo.load(), masjid_name="Masjid Baru", adhan_volume=42)
    repo.save(settings)
    after = json.loads(path.read_text())
    assert after["masjid"]["name"] == "Masjid Baru"
    assert after["adhan_audio"]["volume"] == 42
    # Hand-edited sections survive the round-trip untouched.
    assert after["displays"] == before["displays"]
    assert after["playlists"] == before["playlists"]
    assert after["schedule"]["manual_days"] == before["schedule"]["manual_days"]
    assert after["adhan_audio"]["file"] == before["adhan_audio"]["file"]
    reloaded = repo.load()
    assert reloaded.masjid_name == "Masjid Baru"
    assert reloaded.adhan_volume == 42


# Prayer buffer + manual pins


def test_missing_buffer_is_empty_cache(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    # Pins live behind get_pin now: get_day is a pure buffer read.
    assert repo.get_day(PINNED, ZONE) is None
    assert repo.get_pin(PINNED, ZONE) is not None
    assert repo.get_day(date(2026, 4, 9), ZONE) is None
    # Pins are installation-global (no zone of their own): they match any
    # requested zone, stamped with it — only buffer rows filter by zone.
    pin = repo.last_known(date(2026, 4, 9), "XX99")
    assert pin is not None
    assert pin.source is ScheduleSource.MANUAL
    assert pin.zone == "XX99"


def test_pin_markers_win_over_buffer_row(tmp_path: Path):
    """Per-marker precedence: the pin corrects, the buffer fills the rest."""
    from dataclasses import replace as _replace

    repo, _ = _prayer_repo(tmp_path)
    assert repo.save_day_unless_manual(_jakim_day(PINNED)) is False
    # Complete pin ignores the stored row wholesale...
    day = repo.get_pin(PINNED, ZONE)
    assert day is not None
    assert day.source is ScheduleSource.MANUAL
    assert day.zone == ZONE
    assert day.fajr == time(5, 58)
    # ...while get_day stays a pure buffer read (empty here).
    assert repo.get_day(PINNED, ZONE) is None
    # A stored row does not leak through the pin either.
    repo.save_day(_replace(_jakim_day(PINNED), fajr=time(6, 30)))
    assert repo.get_day(PINNED, ZONE).fajr == time(6, 30)
    assert repo.get_pin(PINNED, ZONE).fajr == time(5, 58)


def test_save_day_unless_manual_pinned_returns_false(tmp_path: Path):
    repo, buffer_path = _prayer_repo(tmp_path)
    assert repo.save_day_unless_manual(_jakim_day(PINNED)) is False
    assert not buffer_path.exists()


def test_save_day_unless_manual_writes_and_persists(tmp_path: Path):
    repo, buffer_path = _prayer_repo(tmp_path)
    target = date(2026, 4, 2)
    assert repo.save_day_unless_manual(_jakim_day(target)) is True
    assert buffer_path.exists()
    fresh_cfg = load_config_file(_copy_example(tmp_path))
    fresh = FilePrayerRepo(buffer_path, fresh_cfg.schedule.manual_days)
    day = fresh.get_day(target, ZONE)
    assert day is not None
    assert day.source is ScheduleSource.JAKIM
    assert day.asr == time(16, 30)


def test_get_day_rejects_zone_mismatch(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    repo.save_day(_jakim_day(date(2026, 4, 2)))
    assert repo.get_day(date(2026, 4, 2), "XX99") is None


def test_last_known_returns_newest_lte_day(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    repo.save_day(_jakim_day(date(2026, 4, 2)))
    repo.save_day(_jakim_day(date(2026, 4, 5)))
    assert repo.last_known(date(2026, 4, 4), ZONE) is not None
    assert repo.last_known(date(2026, 4, 4), ZONE).date == date(2026, 4, 2)
    assert repo.last_known(date(2026, 4, 5), ZONE).date == date(2026, 4, 5)
    # The manual pin is the newest day on or before 04-01.
    assert repo.last_known(PINNED, ZONE).source is ScheduleSource.MANUAL
    assert repo.last_known(date(2026, 3, 31), ZONE) is None


def test_last_known_manual_pin_wins_tie_over_buffer(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    # Force a buffer row onto the pinned date (save_day bypasses the
    # save_day_unless_manual guard) to create the tie.
    repo.save_day(_jakim_day(PINNED))
    assert repo.get_pin(PINNED, ZONE).source is ScheduleSource.MANUAL
    known = repo.last_known(PINNED, ZONE)
    assert known is not None
    assert known.date == PINNED
    assert known.source is ScheduleSource.MANUAL


def test_delete_day_never_removes_config_pins(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    assert repo.delete_day(PINNED, ZONE) is False
    assert repo.get_pin(PINNED, ZONE) is not None
    assert repo.delete_day(date(2026, 4, 9), ZONE) is False


# Playlists


def test_playlist_list_maps_items(tmp_path: Path):
    path = _copy_example(tmp_path)
    repo = FilePlaylistRepo(path)
    playlists = repo.list()
    assert len(playlists) == 1
    assert playlists[0].id == "announcements"
    assert playlists[0].items[0].image_path == "media/playlists/welcome.jpg"
    assert repo.get("announcements") is not None
    assert repo.get("missing") is None


def test_playlist_anchor_name_maps_to_marker(tmp_path: Path):
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["playlists"][0]["anchor_marker"] = "fajr"
    path.write_text(json.dumps(raw))
    repo = FilePlaylistRepo(path)
    assert repo.list()[0].anchor_marker is MarkerName.FAJR


def test_playlist_unknown_anchor_raises_config_error(tmp_path: Path):
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["playlists"][0]["anchor_marker"] = "fajr-nope"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        FilePlaylistRepo(path).list()


# Config errors


def test_invalid_json_raises_config_error(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    with pytest.raises(ConfigError, match="bad.json"):
        load_config_file(bad)
    with pytest.raises(ConfigError):
        FileSettingsRepo(bad).load()


def test_schema_violation_raises_config_error(tmp_path: Path):
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["timing"]["iqamah_rules"] = raw["timing"]["iqamah_rules"][:5]
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match=str(path)):
        load_config_file(path)


def test_unordered_manual_pin_raises_config_error(tmp_path: Path):
    """An out-of-order hand-edit is ConfigError (503), never SyncError (500)."""
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["schedule"]["manual_days"][0]["fajr"] = "14:00"
    path.write_text(json.dumps(raw))
    cfg = load_config_file(path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days)
    with pytest.raises(ConfigError):
        repo.get_pin(PINNED, ZONE)
    with pytest.raises(ConfigError):
        repo.last_known(PINNED, ZONE)
    with pytest.raises(ConfigError):
        repo.validate_pins(ZONE)


def _strip_pin_to(path: Path, keep: list[str]) -> None:
    """Rewrite the example's pin to a partial one (only ``keep`` markers set)."""
    raw = json.loads(path.read_text())
    full = raw["schedule"]["manual_days"][0]
    raw["schedule"]["manual_days"] = [
        {"date": full["date"], **{key: full[key] for key in keep}}
    ]
    path.write_text(json.dumps(raw))


def test_partial_pin_completes_against_buffer_row(tmp_path: Path):
    """Pin markers win; the stored provider row fills the missing ones."""
    path = _copy_example(tmp_path)
    _strip_pin_to(path, ["maghrib"])
    cfg = load_config_file(path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days)
    repo.save_day(_jakim_day(PINNED))
    day = repo.get_pin(PINNED, ZONE)
    assert day is not None
    assert day.source is ScheduleSource.MANUAL
    assert day.maghrib == time(19, 15)  # pin's correction
    assert day.fajr == time(5, 58)  # buffer's row
    assert day.fetched_at == FETCHED  # provider age, honestly kept


def test_partial_pin_without_buffer_row_is_config_error(tmp_path: Path):
    """An uncompletable partial pin fails loud — never drops silently."""
    path = _copy_example(tmp_path)
    _strip_pin_to(path, ["maghrib"])
    cfg = load_config_file(path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days)
    with pytest.raises(ConfigError, match="missing markers"):
        repo.get_pin(PINNED, ZONE)
    with pytest.raises(ConfigError):
        repo.last_known(PINNED, ZONE)
    with pytest.raises(ConfigError):
        repo.validate_pins(ZONE)


def test_playlist_anchor_normalizes_case_and_whitespace(tmp_path: Path):
    """Anchors accept the same case/whitespace folding as window bounds."""
    for anchor in ("Fajr", " FAJR ", "dhuhr"):
        path = _copy_example(tmp_path)
        raw = json.loads(path.read_text())
        raw["playlists"][0]["anchor_marker"] = anchor
        path.write_text(json.dumps(raw))
        assert FilePlaylistRepo(path).list()[0].anchor_marker is MarkerName(
            anchor.strip().lower()
        )


def test_duplicate_manual_day_dates_are_config_error(tmp_path: Path):
    """Two pins for one date fail at load (they would serve divergently)."""
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["schedule"]["manual_days"].append(dict(raw["schedule"]["manual_days"][0]))
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match="duplicate manual_day"):
        load_config_file(path)


def test_playlist_errors_carry_the_playlist_id(tmp_path: Path):
    """One typo in a multi-playlist file points at the offending entry."""
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    raw["playlists"][0]["anchor_marker"] = "bogus"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match="announcements"):
        FilePlaylistRepo(path).list()
    raw["playlists"][0]["anchor_marker"] = None
    raw["playlists"][0]["window_start"] = "bogus"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match="announcements"):
        FilePlaylistRepo(path).list()
