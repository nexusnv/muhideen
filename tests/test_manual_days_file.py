"""Separate manual-days pins file (issue #88).

File-config only: example config copied to tmp_path, pins file written
alongside; no imports from ``muhideen.api`` (adapters only) except the
watcher unit which is stdlib-threading free of app wiring.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from muhideen.adapters.file_config import (
    FileSettingsRepo,
    load_config_file,
    load_manual_days_file,
    manual_days_file_for_config,
    resolve_pins_path,
)
from muhideen.core.errors import ConfigError

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"
PINS_EXAMPLE = (
    Path(__file__).resolve().parent.parent / "config" / "manual_days.example.json"
)


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    return dest


def _example_pin() -> dict:
    raw = json.loads(EXAMPLE.read_text())
    return dict(raw["schedule"]["manual_days"][0])


def _write_pins(tmp_path: Path, payload: object, name: str = "pins.json") -> Path:
    target = tmp_path / name
    target.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return target


def _point_config_at(config_path: Path, ref: str, clear_inline: bool = True) -> None:
    raw = json.loads(config_path.read_text())
    if clear_inline:
        raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = ref
    config_path.write_text(json.dumps(raw))


def test_pins_example_file_is_bare_array():
    payload = json.loads(PINS_EXAMPLE.read_text())
    assert isinstance(payload, list)
    assert {entry["date"] for entry in payload} == {"2026-12-31", "2027-01-01"}


def test_schema_rejects_inline_plus_file():
    from muhideen.adapters.file_models import ConfigFile

    raw = json.loads(EXAMPLE.read_text())
    assert raw["schedule"]["manual_days"]
    raw["schedule"]["manual_days_file"] = "pins.json"
    with pytest.raises(Exception, match="exclusive"):
        ConfigFile.model_validate(raw)


def test_schema_accepts_file_reference_without_inline():
    from muhideen.adapters.file_models import ConfigFile

    raw = json.loads(EXAMPLE.read_text())
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = "config/manual_days.json"
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.manual_days_file == "config/manual_days.json"
    assert cfg.schedule.manual_days == []


def test_resolve_relative_and_absolute(tmp_path: Path):
    config_path = tmp_path / "muhideen.json"
    assert resolve_pins_path(config_path, "pins.json") == tmp_path / "pins.json"
    assert resolve_pins_path(config_path, "config/pins.json") == (
        tmp_path / "config/pins.json"
    )
    assert resolve_pins_path(config_path, "/etc/muhideen/pins.json") == Path(
        "/etc/muhideen/pins.json"
    )


def test_loads_pins_from_relative_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pin = _example_pin()
    pins_path = _write_pins(tmp_path, [pin])
    _point_config_at(config_path, pins_path.name)
    cfg = load_config_file(config_path)
    assert len(cfg.schedule.manual_days) == 1
    assert cfg.schedule.manual_days[0].date == date.fromisoformat(pin["date"])
    assert cfg.schedule.manual_days[0].maghrib == pin["maghrib"]
    assert cfg.schedule.manual_days_file == pins_path.name


def test_loads_pins_from_absolute_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pin = _example_pin()
    pins_path = _write_pins(tmp_path, [pin])
    _point_config_at(config_path, str(pins_path))
    cfg = load_config_file(config_path)
    assert cfg.schedule.manual_days[0].date == date.fromisoformat(pin["date"])


def test_missing_pins_file_fails_loud_naming_pins_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    _point_config_at(config_path, "no-such-pins.json")
    with pytest.raises(ConfigError, match="no-such-pins.json"):
        load_config_file(config_path)


def test_invalid_pins_json_names_pins_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pins_path = _write_pins(tmp_path, "{ not json", name="pins.json")
    _point_config_at(config_path, pins_path.name)
    with pytest.raises(ConfigError) as excinfo:
        load_config_file(config_path)
    assert pins_path.name in str(excinfo.value)


def test_non_array_pins_file_rejected(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pins_path = _write_pins(tmp_path, {"manual_days": []}, name="pins.json")
    _point_config_at(config_path, pins_path.name)
    with pytest.raises(ConfigError, match="bare array"):
        load_config_file(config_path)


def test_duplicate_pins_dates_rejected_naming_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pin = _example_pin()
    pins_path = _write_pins(tmp_path, [pin, dict(pin)], name="pins.json")
    _point_config_at(config_path, pins_path.name)
    with pytest.raises(ConfigError) as excinfo:
        load_config_file(config_path)
    assert "duplicate manual_day" in str(excinfo.value)
    assert pins_path.name in str(excinfo.value)


def test_invalid_pin_entry_names_pins_file(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pins_path = _write_pins(
        tmp_path, [{"date": "2026-12-31", "maghrib": "99:99"}], name="pins.json"
    )
    _point_config_at(config_path, pins_path.name)
    with pytest.raises(ConfigError) as excinfo:
        load_config_file(config_path)
    assert pins_path.name in str(excinfo.value)


def test_dateless_pin_in_file_rejected(tmp_path: Path):
    pins_path = _write_pins(tmp_path, [{"date": "2026-12-31"}], name="pins.json")
    with pytest.raises(ConfigError) as excinfo:
        load_manual_days_file(pins_path)
    assert pins_path.name in str(excinfo.value)


def test_partial_pin_from_file_completes_like_inline(tmp_path: Path):
    """Per-marker merge semantics are identical regardless of pin source."""
    from datetime import UTC, datetime, time

    from muhideen.adapters.file_config import FilePrayerRepo
    from muhideen.core.values import PrayerDay, ScheduleSource

    config_path = _copy_example(tmp_path)
    pin = _example_pin()
    pins_path = _write_pins(
        tmp_path, [{"date": pin["date"], "maghrib": pin["maghrib"]}], name="pins.json"
    )
    _point_config_at(config_path, pins_path.name)
    cfg = load_config_file(config_path)
    buffer_path = tmp_path / "buffer.json"
    repo = FilePrayerRepo(buffer_path, cfg.schedule.manual_days)
    zone = cfg.schedule.effective_zone
    pinned_date = date.fromisoformat(pin["date"])
    repo.save_day(
        PrayerDay(
            date=pinned_date,
            zone=zone,
            imsak=time(5, 48),
            fajr=time(5, 58),
            syuruq=time(7, 5),
            dhuha=time(7, 33),
            dhuhr=time(13, 15),
            asr=time(16, 30),
            maghrib=time(19, 15),
            isha=time(20, 30),
            source=ScheduleSource.JAKIM,
            fetched_at=datetime(2026, 4, 2, 12, 0, tzinfo=UTC),
        )
    )
    day = repo.get_pin(pinned_date, zone)
    assert day is not None
    assert day.source is ScheduleSource.MANUAL
    assert day.maghrib == time(19, 15)


def test_manual_days_file_for_config(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    assert manual_days_file_for_config(config_path) is None
    _point_config_at(config_path, "pins.json")
    assert manual_days_file_for_config(config_path) == tmp_path / "pins.json"
    _point_config_at(config_path, "/etc/muhideen/pins.json")
    assert manual_days_file_for_config(config_path) == Path("/etc/muhideen/pins.json")


def test_save_round_trips_without_touching_reference(tmp_path: Path):
    config_path = _copy_example(tmp_path)
    pin = _example_pin()
    _write_pins(tmp_path, [pin], name="pins.json")
    _point_config_at(config_path, "pins.json")
    repo = FileSettingsRepo(config_path)
    settings = repo.load()
    repo.save(settings)
    raw = json.loads(config_path.read_text())
    assert raw["schedule"]["manual_days_file"] == "pins.json"
    assert raw["schedule"]["manual_days"] == []
    reloaded = repo.load()
    assert len(reloaded_settings_pins(config_path)) == 1
    assert reloaded.masjid_name == settings.masjid_name


def reloaded_settings_pins(config_path: Path) -> list:
    return load_config_file(config_path).schedule.manual_days


def test_watcher_watch_adds_new_path(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    first = _copy_example(tmp_path)
    watcher = ConfigWatcher([first], lambda: None, interval_s=60.0)
    assert len(watcher._paths) == 1
    extra = tmp_path / "pins.json"
    extra.write_text("[]")
    watcher.watch(extra)
    assert extra in watcher._paths
    before = len(watcher._paths)
    watcher.watch(extra)
    assert len(watcher._paths) == before
