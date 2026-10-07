"""File-backed repos over the core ports (task 2).

CWD-independent: example config is located via ``Path(__file__)`` and copied
into ``tmp_path``; no test imports from ``muhideen.api`` (adapters only).
"""

from __future__ import annotations

import json
import os
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


def test_settings_concurrent_save_serializes_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Concurrent saves through one repo never overlap their read-modify-write."""
    import threading

    import muhideen.adapters.file_config as file_config_module

    repo, path = _settings_repo(tmp_path)
    base = repo.load()
    workers = 8
    rounds = 20
    start = threading.Barrier(workers)
    active = 0
    peak = 0
    count_lock = threading.Lock()
    real_write = file_config_module._atomic_write_json

    def _tracking_write(target: Path, payload: dict) -> None:
        nonlocal active, peak
        with count_lock:
            active += 1
            peak = max(peak, active)
        try:
            real_write(target, payload)
        finally:
            with count_lock:
                active -= 1

    monkeypatch.setattr(file_config_module, "_atomic_write_json", _tracking_write)
    names = {
        f"Masjid Race {worker}-{round_}"
        for worker in range(workers)
        for round_ in range(rounds)
    }
    errors: list[Exception] = []

    def _race(worker: int) -> None:
        try:
            start.wait()
            for round_ in range(rounds):
                repo.save(replace(base, masjid_name=f"Masjid Race {worker}-{round_}"))
                repo.load()
        except Exception as exc:  # pragma: no cover - failure path asserts below
            errors.append(exc)

    threads = [
        threading.Thread(target=_race, args=(worker,)) for worker in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    # Atomic renames keep every write a complete payload: the file always
    # parses and the winner is one intact save, never a torn mix.
    assert json.loads(path.read_text())["masjid"]["name"] in names
    assert repo.load().masjid_name in names
    # The read-modify-write bodies never overlapped: serialized in-process.
    assert peak == 1, f"overlapping concurrent saves observed (peak={peak})"


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
        repo.validate_pins(cfg.schedule.manual_days, ZONE)


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
        repo.validate_pins(cfg.schedule.manual_days, ZONE)


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


def _buffer_file(tmp_path: Path, payload: object) -> Path:
    """Write a raw prayer buffer file; return its path."""
    path = tmp_path / "buffer.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def _buffer_entry(zone: str = ZONE, **overrides: str) -> dict[str, str]:
    """One buffer day entry mirroring the example pin (HH:MM strings)."""
    entry = {
        "zone": zone,
        "source": ScheduleSource.JAKIM.value,
        "fetched_at": FETCHED.isoformat(),
        **{key: value.strftime("%H:%M") for key, value in _TIMES.items()},
    }
    entry.update(overrides)
    return entry


def test_settings_path_property(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    assert repo.path == path


def test_settings_load_wraps_domain_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Domain construction failures surface as ConfigError, never ValueError."""
    import muhideen.adapters.file_config as file_config_module

    def _boom(cfg: object) -> object:
        raise ValueError("bad domain")

    monkeypatch.setattr(file_config_module, "_settings_from_config", _boom)
    repo, _ = _settings_repo(tmp_path)
    with pytest.raises(ConfigError, match="bad domain"):
        repo.load()


def test_settings_save_missing_file_is_config_error(tmp_path: Path):
    repo, _ = _settings_repo(tmp_path)
    settings = repo.load()
    missing = FileSettingsRepo(tmp_path / "no-such-dir" / "muhideen.json")
    with pytest.raises(ConfigError, match="cannot read config file"):
        missing.save(settings)


def test_settings_save_invalid_json_is_config_error(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = repo.load()
    path.write_text("{bogus")
    with pytest.raises(ConfigError, match="invalid JSON"):
        repo.save(settings)


def test_settings_save_non_object_root_is_config_error(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = repo.load()
    path.write_text("[]")
    with pytest.raises(ConfigError, match="must be an object"):
        repo.save(settings)


def test_settings_save_missing_sections_is_config_error(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = repo.load()
    raw = json.loads(path.read_text())
    del raw["schedule"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match="missing schedule/audio"):
        repo.save(settings)


def test_settings_save_rejects_invalid_round_trip(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = repo.load()
    raw = json.loads(path.read_text())
    raw["bogus_root_key"] = True
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        repo.save(settings)


def test_atomic_write_skips_dir_fsync_when_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Platforms without directory fsync still persist the payload."""
    from muhideen.adapters.file_config import _atomic_write_json

    def _no_dir_fd(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags & os.O_DIRECTORY:
            raise OSError("no directory fsync here")
        return real_open(path, flags, *args, **kwargs)

    real_open = os.open
    monkeypatch.setattr(os, "open", _no_dir_fd)
    target = tmp_path / "buffer.json"
    _atomic_write_json(target, {"days": {}})
    assert json.loads(target.read_text()) == {"days": {}}


def test_atomic_write_ignores_dir_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from muhideen.adapters.file_config import _atomic_write_json

    calls = {"count": 0}
    real_fsync = os.fsync

    def _flaky_fsync(fd: int) -> None:
        calls["count"] += 1
        if calls["count"] >= 2:
            raise OSError("dir fsync unavailable")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _flaky_fsync)
    target = tmp_path / "buffer.json"
    _atomic_write_json(target, {"days": {}})
    assert json.loads(target.read_text()) == {"days": {}}


def test_partial_pin_unordered_after_completion_is_config_error(tmp_path: Path):
    """A partial pin that breaks ordering once completed is ConfigError."""
    path = _copy_example(tmp_path)
    raw = json.loads(path.read_text())
    pin_date = raw["schedule"]["manual_days"][0]["date"]
    raw["schedule"]["manual_days"] = [{"date": pin_date, "fajr": "14:00"}]
    path.write_text(json.dumps(raw))
    cfg = load_config_file(path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days)
    repo.save_day(_jakim_day(PINNED))
    with pytest.raises(ConfigError, match="invalid manual_day"):
        repo.get_pin(PINNED, ZONE)


def test_validate_buffer_accepts_seeded_days(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    repo.save_day(_jakim_day(PINNED))
    repo.save_day(_jakim_day(date(2026, 4, 2)))
    repo.validate_buffer()


def test_validate_buffer_rejects_bad_date_key(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {"bogus": _buffer_entry()}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="bogus"):
        repo.validate_buffer()


def test_validate_buffer_rejects_non_object_entry(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): [1, 2]}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="not an object"):
        repo.validate_buffer()


def test_validate_buffer_rejects_corrupt_times(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): _buffer_entry(fajr="xx")}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.validate_buffer()


def test_set_manual_days_refreshes_snapshot(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    assert repo.get_pin(PINNED, ZONE) is None
    config_path = _copy_example(tmp_path)
    cfg = load_config_file(config_path)
    repo.set_manual_days(cfg.schedule.manual_days)
    assert repo.get_pin(PINNED, ZONE) is not None


def test_validate_pins_accepts_empty(tmp_path: Path):
    repo, _ = _prayer_repo(tmp_path)
    repo.validate_pins([], ZONE)


def test_validate_pins_checks_new_pins_not_snapshot(tmp_path: Path):
    """Reload validation checks incoming pins, never the stale snapshot."""
    config_path = _copy_example(tmp_path)
    good_cfg = load_config_file(config_path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", good_cfg.schedule.manual_days)
    raw = json.loads(config_path.read_text())
    raw["schedule"]["manual_days"][0]["fajr"] = "14:00"
    config_path.write_text(json.dumps(raw))
    bad_cfg = load_config_file(config_path)
    with pytest.raises(ConfigError):
        repo.validate_pins(bad_cfg.schedule.manual_days, ZONE)
    # ...and the live snapshot still serves the last-good pin.
    assert repo.get_pin(PINNED, ZONE) is not None
    # A repo holding stale-bad pins still accepts good incoming pins.
    stale = FilePrayerRepo(tmp_path / "buffer.json", bad_cfg.schedule.manual_days)
    stale.validate_pins(good_cfg.schedule.manual_days, ZONE)


def test_read_buffer_unreadable_is_config_error(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path, [])  # a directory, not a file
    with pytest.raises(ConfigError, match="cannot read prayer buffer"):
        repo.get_day(PINNED, ZONE)


def test_read_buffer_invalid_json_is_config_error(tmp_path: Path):
    _buffer_file(tmp_path, "{bogus")
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="invalid JSON"):
        repo.get_day(PINNED, ZONE)


def test_read_buffer_non_object_is_config_error(tmp_path: Path):
    _buffer_file(tmp_path, [1, 2, 3])
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="must be an object"):
        repo.get_day(PINNED, ZONE)


def test_read_buffer_non_object_days_is_config_error(tmp_path: Path):
    _buffer_file(tmp_path, {"days": [1]})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="days must be an object"):
        repo.get_day(PINNED, ZONE)


def test_buffer_day_ignores_non_mapping_days(tmp_path: Path):
    """The pure buffer read tolerates a non-mapping days section."""
    repo, _ = _prayer_repo(tmp_path)
    assert repo._buffer_day({"days": []}, PINNED, ZONE) is None


def test_buffer_day_rejects_corrupt_entry(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): _buffer_entry(fajr="xx")}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.get_day(PINNED, ZONE)


def test_save_day_unless_manual_writes_through_when_unpinned(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    assert repo.save_day_unless_manual(_jakim_day(PINNED)) is True
    assert repo.get_day(PINNED, ZONE) is not None


def test_save_day_unless_manual_respects_manual_buffer_row(tmp_path: Path):
    """A manual-sourced buffer row blocks the sync write like a pin does."""
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    manual = replace(_jakim_day(PINNED), source=ScheduleSource.MANUAL)
    repo.save_day(manual)
    assert repo.save_day_unless_manual(_jakim_day(PINNED)) is False
    assert repo.get_day(PINNED, ZONE) == manual


def test_last_known_skips_superseded_uncompletable_pin(tmp_path: Path):
    """An older partial pin without its row must not poison newer history."""
    path = _copy_example(tmp_path)
    _strip_pin_to(path, ["maghrib"])
    cfg = load_config_file(path)
    repo = FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days)
    repo.save_day(_jakim_day(date(2026, 4, 5)))
    known = repo.last_known(date(2026, 4, 5), ZONE)
    assert known is not None
    assert known.date == date(2026, 4, 5)
    assert known.source is ScheduleSource.JAKIM


def test_last_known_returns_none_when_empty(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    assert repo.last_known(PINNED, ZONE) is None


def test_last_known_rejects_bad_date_key(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {"bogus": _buffer_entry()}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="bogus"):
        repo.last_known(PINNED, ZONE)


def test_last_known_rejects_non_object_entry(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): [1]}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="not an object"):
        repo.last_known(PINNED, ZONE)


def test_last_known_rejects_corrupt_entry(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): _buffer_entry(fajr="xx")}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.last_known(PINNED, ZONE)


def test_delete_day_removes_manual_buffer_row(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    manual = replace(_jakim_day(PINNED), source=ScheduleSource.MANUAL)
    repo.save_day(manual)
    assert repo.delete_day(PINNED, ZONE) is True
    assert repo.get_day(PINNED, ZONE) is None


def test_delete_day_keeps_provider_rows(tmp_path: Path):
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    repo.save_day(_jakim_day(PINNED))
    assert repo.delete_day(PINNED, ZONE) is False
    assert repo.get_day(PINNED, ZONE) is not None
    foreign = replace(
        _jakim_day(date(2026, 4, 2)), source=ScheduleSource.MANUAL, zone="XX99"
    )
    repo.save_day(foreign)
    assert repo.delete_day(date(2026, 4, 2), ZONE) is False


def test_delete_day_rejects_corrupt_entry(tmp_path: Path):
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): _buffer_entry(fajr="xx")}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.delete_day(PINNED, ZONE)


def test_playlist_value_error_carries_the_playlist_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import muhideen.adapters.file_config as file_config_module

    def _boom(playlist: object) -> object:
        raise ValueError("bad window shape")

    monkeypatch.setattr(file_config_module, "parse_window", _boom)
    path = _copy_example(tmp_path)
    with pytest.raises(ConfigError, match="announcements"):
        FilePlaylistRepo(path).list()


def test_playlist_get_hit_and_miss(tmp_path: Path):
    path = _copy_example(tmp_path)
    repo = FilePlaylistRepo(path)
    assert repo.get("announcements") is not None
    assert repo.get("no-such-playlist") is None


def test_unordered_buffer_row_is_corrupt_everywhere(tmp_path: Path):
    """Parseable-but-unordered rows fail loud, never serve wrong times."""
    _buffer_file(tmp_path, {"days": {PINNED.isoformat(): _buffer_entry(fajr="14:00")}})
    repo = FilePrayerRepo(tmp_path / "buffer.json", [])
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.get_day(PINNED, ZONE)
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.last_known(PINNED, ZONE)
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.validate_buffer()
    with pytest.raises(ConfigError, match="corrupt buffer day"):
        repo.delete_day(PINNED, ZONE)


def test_settings_load_maps_adhan_file_and_aladhan(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    raw = json.loads(path.read_text())
    raw["adhan_audio"]["file"] = "custom/x.mp3"
    raw["schedule"]["aladhan"]["method"] = 2
    path.write_text(json.dumps(raw, indent=2) + "\n")
    settings = repo.load()
    assert settings.adhan_audio_file == "custom/x.mp3"
    assert settings.aladhan_method == 2


def test_settings_save_round_trips_zone_provider_aladhan_and_file(tmp_path: Path):
    repo, _ = _settings_repo(tmp_path)
    settings = repo.load()
    changed = replace(
        settings,
        sync_provider="aladhan",
        zone="my-masjid",
        jakim_zone="SGR01",
        lat=3.07,
        lon=101.69,
        aladhan_base_url="https://aladhan.api.islamic.network/v1",
        aladhan_method=2,
        adhan_audio_file="custom/adhan2.mp3",
    )
    repo.save(changed)
    reloaded = repo.load()
    assert reloaded.sync_provider == "aladhan"
    assert reloaded.zone == "my-masjid"
    assert reloaded.jakim_zone == "SGR01"
    assert reloaded.aladhan_base_url == "https://aladhan.api.islamic.network/v1"
    assert reloaded.aladhan_method == 2
    assert reloaded.adhan_audio_file == "custom/adhan2.mp3"


def test_settings_save_clears_zone_override_when_matching_fetch_key(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = replace(repo.load(), zone="SGR01", jakim_zone="SGR01")
    repo.save(settings)
    raw = json.loads(path.read_text())
    assert raw["schedule"]["zone"] is None
    assert repo.load().zone == "SGR01"


def test_settings_save_keeps_jakim_key_none_for_non_jakim_provider(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    settings = replace(
        repo.load(),
        sync_provider="aladhan",
        zone="my-label",
        jakim_zone=None,
        lat=3.07,
        lon=101.69,
    )
    repo.save(settings)
    raw = json.loads(path.read_text())
    assert raw["schedule"]["jakim"]["zone"] is None
    reloaded = repo.load()
    assert reloaded.jakim_zone is None and reloaded.zone == "my-label"


def test_settings_save_materializes_missing_sections(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    raw = json.loads(path.read_text())
    del raw["schedule"]["aladhan"]
    del raw["adhan_audio"]
    path.write_text(json.dumps(raw, indent=2) + "\n")
    settings = repo.load()
    repo.save(settings)
    reloaded = repo.load()
    assert reloaded.aladhan_base_url == settings.aladhan_base_url
    assert reloaded.adhan_audio_file == settings.adhan_audio_file
    raw = json.loads(path.read_text())
    assert raw["schedule"]["aladhan"]["base_url"] == settings.aladhan_base_url
    assert raw["adhan_audio"]["file"] == settings.adhan_audio_file


def test_atomic_write_json_concurrent_writers_keep_valid_json(tmp_path: Path):
    import threading

    from muhideen.adapters.file_config import _atomic_write_json

    path = tmp_path / "config.json"
    path.write_text("{}\n")
    payloads = [{"worker": worker, "pad": "x" * 2000} for worker in range(8)]
    errors: list[Exception] = []

    def write_many(payload: dict) -> None:
        try:
            for _ in range(50):
                _atomic_write_json(path, payload)
        except Exception as exc:  # pragma: no cover - failure path asserts below
            errors.append(exc)

    threads = [
        threading.Thread(target=write_many, args=(payload,)) for payload in payloads
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    # The winner must be one complete payload, never a torn mix, and no
    # tmp file may be left behind.
    assert json.loads(path.read_text()) in payloads
    assert list(tmp_path.glob("*.tmp")) == []


def test_settings_save_materializes_missing_jakim_for_non_jakim(tmp_path: Path):
    repo, path = _settings_repo(tmp_path)
    raw = json.loads(path.read_text())
    del raw["schedule"]["jakim"]
    raw["schedule"]["sync_provider"] = "none"
    path.write_text(json.dumps(raw, indent=2) + "\n")
    settings = repo.load()
    assert settings.sync_provider == "none"
    repo.save(settings)
    # The section must exist in the JSON itself: Schedule.jakim has a
    # default factory, so a reload-only check would pass even if save left
    # the section deleted.
    raw = json.loads(path.read_text())
    assert raw["schedule"]["jakim"] == {"zone": None}
    reloaded = repo.load()
    assert reloaded.sync_provider == "none"
    assert reloaded.jakim_zone is None
