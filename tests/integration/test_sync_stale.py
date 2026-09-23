"""Sync-to-stale acceptance tests (slice 1A-6, Task 6).

Composition guards over already-landed output: `run_sync` (Task 5) writes
JAKIM rows that the engine resolves through `resolve_fallback`/`is_stale`
(`STALE_AFTER = 48h` or degraded source, FR-1.1). Expected to PASS on
first run — a failure pinpoints a defect in Task 3/5 output, not here.

File-local fakes duplicated per TESTING_STRATEGY.md:19; harness style
copied from tests/integration/test_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from muhideen.adapters.scheduler import run_sync
from muhideen.core.values import PrayerDay, ScheduleSource, Settings
from muhideen.engine import Engine

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=TZ)
TODAY = date(2026, 9, 24)
ZONE = "SGR01"


class FakeSettingsRepo:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> Settings:
        return self.settings

    def save(self, settings: Settings) -> None:
        self.settings = settings


class FakePrayerRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[date, str], PrayerDay] = {}

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        return self.rows.get((day, zone))

    def save_day(self, prayer_day: PrayerDay) -> None:
        self.rows[(prayer_day.date, prayer_day.zone)] = prayer_day

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        candidates = [
            row
            for (row_date, row_zone), row in self.rows.items()
            if row_zone == zone and row_date <= day
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: row.date)


class FakeClock:
    """Pinned clock; tests move `.current` to advance time."""

    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return 0.0


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[str] = []

    def publish(self, event: str) -> None:
        self.events.append(event)


class FakeJAKIMClient:
    """Returns preset rows stamped with the injected clock (like real parse)."""

    def __init__(self, days: list[PrayerDay], clock: FakeClock) -> None:
        self._days = days
        self._clock = clock

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        return [
            replace(day, zone=zone, fetched_at=self._clock.now()) for day in self._days
        ]


def _settings() -> Settings:
    return Settings(masjid_name="Masjid Test", zone=ZONE, hijri_offset=0)


def _day(
    day: date,
    *,
    source: ScheduleSource = ScheduleSource.JAKIM,
    fetched_at: datetime = NOW,
) -> PrayerDay:
    return PrayerDay(
        date=day,
        zone=ZONE,
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=source,
        fetched_at=fetched_at,
    )


@dataclass
class Harness:
    settings: FakeSettingsRepo
    repo: FakePrayerRepo
    clock: FakeClock
    bus: RecordingBus
    engine: Engine


def _harness() -> Harness:
    settings_repo = FakeSettingsRepo(_settings())
    prayer_repo = FakePrayerRepo()
    clock = FakeClock(NOW)
    bus = RecordingBus()
    engine = Engine(
        settings_repo=settings_repo,
        prayer_repo=prayer_repo,
        clock=clock,
        event_bus=bus,
    )
    return Harness(settings_repo, prayer_repo, clock, bus, engine)


def _sync(harness: Harness) -> int:
    client = FakeJAKIMClient([_day(TODAY)], clock=harness.clock)
    return run_sync(
        client=client,
        prayer_repo=harness.repo,
        settings_repo=harness.settings,
        clock=harness.clock,
    )


def test_fresh_jakim_day_is_not_stale() -> None:
    harness = _harness()
    assert _sync(harness) == 1
    result = harness.engine.resolve_day(TODAY, ZONE, harness.clock.now())
    assert result.stale is False


def test_jakim_day_older_than_48h_is_stale() -> None:
    harness = _harness()
    assert _sync(harness) == 1
    harness.clock.current = harness.clock.current + timedelta(hours=49)
    result = harness.engine.resolve_day(TODAY, ZONE, harness.clock.now())
    assert result.stale is True


def test_calc_sourced_day_is_stale() -> None:
    harness = _harness()
    harness.repo.save_day(_day(TODAY, source=ScheduleSource.CALC, fetched_at=NOW))
    result = harness.engine.resolve_day(TODAY, ZONE, NOW)
    assert result.stale is True
