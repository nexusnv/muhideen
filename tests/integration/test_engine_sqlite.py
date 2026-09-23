"""Engine over real SQLite: port conformance (slice 1A-5, Task 5).

Compositional verification only — no source change: the existing Engine
consumes this slice's repos exactly as it consumed the in-memory fakes
(TESTING_STRATEGY.md:11, "engine + real SQLite (tmp file, WAL)").
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import (
    Database,
    SqlitePrayerRepo,
    SqliteSettingsRepo,
)
from muhideen.core.errors import ConfigError
from muhideen.core.values import MarkerName, PrayerDay, ScheduleSource, Settings
from muhideen.engine import Engine

pytestmark = pytest.mark.integration

_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_NOW = datetime(2025, 10, 20, 12, 9, tzinfo=_TZ)  # Monday, one minute pre-window


class FakeClock:
    """File-local pinned clock (TESTING_STRATEGY.md:17-18)."""

    def now(self) -> datetime:
        return _NOW

    def monotonic(self) -> float:
        return 0.0


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[str] = []

    def publish(self, event: str) -> None:
        self.events.append(event)


def _day() -> PrayerDay:
    return PrayerDay(
        date=_NOW.date(),
        zone="SGR01",
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=_NOW - timedelta(hours=1),
    )


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    return database


def _settings() -> Settings:
    return Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0)


def test_engine_resolves_day_saved_through_sqlite(tmp_path: Path) -> None:
    db = _db(tmp_path)
    SqliteSettingsRepo(db).save(_settings())
    day = _day()
    SqlitePrayerRepo(db).save_day(day)
    engine = Engine(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        clock=FakeClock(),
        event_bus=RecordingBus(),
        calc=None,
    )
    result = engine.resolve_day(_NOW.date(), "SGR01", _NOW)
    assert result.day == day
    assert result.stale is False  # fetched 1h ago from JAKIM


def test_engine_reflects_settings_saved_through_sqlite(tmp_path: Path) -> None:
    db = _db(tmp_path)
    settings_repo = SqliteSettingsRepo(db)
    settings = _settings()
    settings_repo.save(settings)
    SqlitePrayerRepo(db).save_day(_day())
    engine = Engine(
        settings_repo=settings_repo,
        prayer_repo=SqlitePrayerRepo(db),
        clock=FakeClock(),
        event_bus=RecordingBus(),
        calc=None,
    )
    first = engine.next_event(_NOW)
    assert first.iqamah_at == datetime(2025, 10, 20, 12, 25, tzinfo=_TZ)
    # Admin edit persists through the real repo and hot-reloads (FR-6.1).
    custom = replace(
        settings,
        iqamah_rules=tuple(
            replace(rule, delay_minutes=15)
            if rule.prayer is MarkerName.DHUHR
            else rule
            for rule in settings.iqamah_rules
        ),
    )
    settings_repo.save(custom)
    second = engine.next_event(_NOW)
    assert second.iqamah_at == datetime(2025, 10, 20, 12, 30, tzinfo=_TZ)


def test_engine_before_setup_raises_config_error(tmp_path: Path) -> None:
    db = _db(tmp_path)  # migrated but never set up: no identity rows
    engine = Engine(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        clock=FakeClock(),
        event_bus=RecordingBus(),
        calc=None,
    )
    with pytest.raises(ConfigError, match="setup"):
        engine.next_event(_NOW)
