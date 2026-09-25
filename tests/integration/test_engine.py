"""Engine orchestration over in-memory fakes (slice 1A-4, Tasks 2-4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from muhideen.api.dto import NextEventDTO
from muhideen.core.errors import ConfigError, ScheduleError
from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    IqamahRule,
    MarkerName,
    PrayerDay,
    PrayerState,
    ScheduleSource,
    Settings,
)
from muhideen.domain import FallbackResult
from muhideen.engine import Engine

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")
NOW = datetime(2025, 10, 20, 12, 0, tzinfo=TZ)
DAY = date(2025, 10, 20)
ZONE = "SGR01"
FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


class FakeSettingsRepo:
    """In-memory settings with a load counter (FR-6.1: reload per call)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.load_count = 0

    def load(self) -> Settings:
        self.load_count += 1
        return self.settings

    def save(self, settings: Settings) -> None:
        self.settings = settings


class FakePrayerRepo:
    """Row store keyed by (date, zone) with a logged last_known path."""

    def __init__(self, rows: dict[tuple[date, str], PrayerDay] | None = None) -> None:
        self.rows: dict[tuple[date, str], PrayerDay] = rows or {}
        self.last_known_calls: list[tuple[date, str]] = []

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        return self.rows.get((day, zone))

    def save_day(self, prayer_day: PrayerDay) -> None:
        self.rows[(prayer_day.date, prayer_day.zone)] = prayer_day

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        self.last_known_calls.append((day, zone))
        candidates = [
            row
            for (row_date, row_zone), row in self.rows.items()
            if row_zone == zone and row_date <= day
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: row.date)


class FakeCalc:
    """Calc port double: records calls, draws a CALC day, can raise."""

    def __init__(self, returned_zone: str = ZONE) -> None:
        self.returned_zone = returned_zone
        self.calls: list[tuple[date, float, float, str, int, int]] = []
        self.error: Exception | None = None

    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = 10,
        dhuha_offset_min: int = 28,
    ) -> PrayerDay:
        self.calls.append((day, lat, lon, method, imsak_offset_min, dhuha_offset_min))
        if self.error is not None:
            raise self.error
        return PrayerDay(
            date=day,
            zone=self.returned_zone,
            imsak=time(5, 40),
            fajr=time(5, 50),
            syuruq=time(7, 0),
            dhuha=time(7, 30),
            dhuhr=time(12, 20),
            asr=time(15, 35),
            maghrib=time(18, 10),
            isha=time(19, 30),
            source=ScheduleSource.CALC,
            fetched_at=datetime.combine(day, time(0, 0), tzinfo=TZ),
        )


class FakeClock:
    """Pinned clock; tests move `.current` to advance time."""

    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return 0.0


class RecordingBus:
    """Event bus double: records published event names in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def publish(self, event: str) -> None:
        self.events.append(event)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "masjid_name": "Masjid Test",
        "zone": ZONE,
        "hijri_offset": 0,
        "iqamah_rules": DEFAULT_IQAMAH_RULES,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _day(
    day: date,
    *,
    zone: str = ZONE,
    source: ScheduleSource = ScheduleSource.JAKIM,
    fetched_at: datetime = NOW - timedelta(hours=1),
    fajr: time = time(5, 45),
    dhuhr: time = time(12, 15),
) -> PrayerDay:
    return PrayerDay(
        date=day,
        zone=zone,
        imsak=time(5, 35),
        fajr=fajr,
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=dhuhr,
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


def _harness(
    *,
    settings: Settings | None = None,
    rows: tuple[PrayerDay, ...] = (),
    calc: FakeCalc | None = None,
    now: datetime = NOW,
) -> Harness:
    settings_repo = FakeSettingsRepo(settings or _settings())
    prayer_repo = FakePrayerRepo({(row.date, row.zone): row for row in rows})
    clock = FakeClock(now)
    bus = RecordingBus()
    engine = Engine(
        settings_repo=settings_repo,
        prayer_repo=prayer_repo,
        clock=clock,
        event_bus=bus,
        calc=calc,
    )
    return Harness(settings_repo, prayer_repo, clock, bus, engine)


# --- Task 2: resolve_day (FR-1.2 fallback chain over ports) ----------------


@pytest.mark.parametrize("returned_zone", ["SGR01", "OTHER"])
def test_cache_miss_uses_calc_and_normalises_zone(returned_zone: str) -> None:
    calc = FakeCalc(returned_zone=returned_zone)
    harness = _harness(settings=_settings(lat=3.1, lon=101.6), calc=calc)
    result = harness.engine.resolve_day(DAY, ZONE, NOW)
    assert result.day.zone == ZONE
    assert result.day.source is ScheduleSource.CALC
    assert result.stale is True
    assert calc.calls == [(DAY, 3.1, 101.6, "MABIMS", 10, 28)]
    assert harness.repo.last_known_calls == []


def test_resolve_day_unknown_zone_raises_schedule_error() -> None:
    calc = FakeCalc()
    harness = _harness(settings=_settings(lat=3.1, lon=101.6), calc=calc)
    with pytest.raises(ScheduleError, match="unknown zone"):
        harness.engine.resolve_day(DAY, "ZZZ99", NOW)
    assert calc.calls == []


def test_cache_hit_fresh_skips_calc_and_last_known() -> None:
    seed = _day(DAY, fetched_at=NOW - timedelta(hours=1))
    calc = FakeCalc()
    harness = _harness(rows=(seed,), calc=calc)
    result = harness.engine.resolve_day(DAY, ZONE, NOW)
    assert isinstance(result, FallbackResult)
    assert result.day == seed
    assert result.stale is False
    assert calc.calls == []
    assert harness.repo.last_known_calls == []


def test_cache_miss_calc_disabled_falls_to_last_known() -> None:
    older = _day(date(2025, 10, 19))
    harness = _harness(rows=(older,), calc=None)
    result = harness.engine.resolve_day(DAY, ZONE, NOW)
    assert result.day == older
    assert result.stale is True


def test_settings_without_coordinates_disable_calc() -> None:
    older = _day(date(2025, 10, 19))
    calc = FakeCalc()
    harness = _harness(rows=(older,), calc=calc)  # default settings: lat/lon None
    result = harness.engine.resolve_day(DAY, ZONE, NOW)
    assert result.day == older
    assert result.stale is True
    assert calc.calls == []


@pytest.mark.parametrize(
    "error",
    [ScheduleError("boom"), ValueError("bad ephemeris")],
    ids=["schedule", "value"],
)
def test_calc_failure_falls_to_last_known(error: Exception) -> None:
    older = _day(date(2025, 10, 19))
    calc = FakeCalc()
    calc.error = error
    harness = _harness(settings=_settings(lat=3.1, lon=101.6), rows=(older,), calc=calc)
    result = harness.engine.resolve_day(DAY, ZONE, NOW)
    assert result.day == older
    assert result.stale is True
    assert len(calc.calls) == 1


def test_all_sources_miss_raises_schedule_error() -> None:
    harness = _harness(calc=None)
    with pytest.raises(ScheduleError) as excinfo:
        harness.engine.resolve_day(DAY, ZONE, NOW)
    assert excinfo.value.zone == ZONE
    assert excinfo.value.date == "2025-10-20"


def test_resolve_day_loads_settings_once() -> None:
    harness = _harness(rows=(_day(DAY),))
    harness.engine.resolve_day(DAY, ZONE, NOW)
    assert harness.settings.load_count == 1


# --- Task 3: next_event (settings -> day -> PRD section 8 event) ----------


def _rules(dhuhr_delay: int) -> tuple[IqamahRule, ...]:
    """DEFAULT_IQAMAH_RULES with an overridden Dhuhr delay."""
    return tuple(
        replace(rule, delay_minutes=dhuhr_delay)
        if rule.prayer is MarkerName.DHUHR
        else rule
        for rule in DEFAULT_IQAMAH_RULES
    )


def test_engine_replays_contract_fixture_vector() -> None:
    """Engine output round-trips api/fixtures/next-event.json via the DTO."""
    day_payload = json.loads((FIXTURES / "prayer-day.json").read_text())
    event_payload = json.loads((FIXTURES / "next-event.json").read_text())
    prayers = day_payload["prayers"]
    boundaries = day_payload["boundaries"]
    now = datetime.fromisoformat(event_payload["now"])
    seed = PrayerDay(
        date=date.fromisoformat(day_payload["date"]),
        zone=day_payload["zone"],
        imsak=time.fromisoformat(boundaries["imsak"]),
        fajr=time.fromisoformat(prayers["fajr"]),
        syuruq=time.fromisoformat(boundaries["syuruq"]),
        dhuha=time.fromisoformat(boundaries["dhuha"]),
        dhuhr=time.fromisoformat(prayers["dhuhr"]),
        asr=time.fromisoformat(prayers["asr"]),
        maghrib=time.fromisoformat(prayers["maghrib"]),
        isha=time.fromisoformat(prayers["isha"]),
        source=ScheduleSource.JAKIM,
        fetched_at=now - timedelta(hours=11),
    )
    harness = _harness(
        settings=_settings(iqamah_rules=_rules(15), boundary_countdown=True),
        rows=(seed,),
        now=now,
    )
    payload = NextEventDTO.from_domain(harness.engine.next_event(now)).model_dump(
        mode="json"
    )
    assert payload == event_payload


def test_settings_reloaded_every_call_without_restart() -> None:
    """No engine-level settings cache: admin edits land live (FR-6.1)."""
    now = datetime(2025, 10, 20, 12, 19, tzinfo=TZ)
    harness = _harness(rows=(_day(DAY),), now=now)
    first = harness.engine.next_event(now)
    assert first.state is PrayerState.IQAMAH_COUNTDOWN
    harness.settings.settings = replace(harness.settings.settings, adhan_duration_s=300)
    second = harness.engine.next_event(now)
    assert second.state is PrayerState.ADHAN
    assert harness.settings.load_count == 2


def test_settings_loaded_once_per_next_event() -> None:
    harness = _harness(rows=(_day(DAY),))
    harness.engine.next_event(NOW)
    assert harness.settings.load_count == 1


def test_midnight_uses_tomorrow_row() -> None:
    now = datetime(2025, 10, 20, 20, 30, tzinfo=TZ)
    today = _day(DAY, fetched_at=now - timedelta(hours=1))
    tomorrow = _day(
        date(2025, 10, 21), fajr=time(5, 47), fetched_at=now - timedelta(hours=1)
    )
    harness = _harness(rows=(today, tomorrow), now=now)
    event = harness.engine.next_event(now)
    assert event.state is PrayerState.NORMAL
    assert event.next_prayer is MarkerName.FAJR
    assert event.adhan_at == datetime.combine(
        date(2025, 10, 21), time(5, 47), tzinfo=TZ
    )
    assert event.iqamah_at == event.adhan_at + timedelta(minutes=15)
    assert event.dim_until == event.iqamah_at + timedelta(minutes=20)
    assert event.stale is False


def test_midnight_tomorrow_from_calc() -> None:
    now = datetime(2025, 10, 20, 20, 30, tzinfo=TZ)
    calc = FakeCalc()
    harness = _harness(
        settings=_settings(lat=3.1, lon=101.6), rows=(_day(DAY),), calc=calc, now=now
    )
    event = harness.engine.next_event(now)
    assert event.adhan_at == datetime.combine(
        date(2025, 10, 21), time(5, 50), tzinfo=TZ
    )
    assert calc.calls == [(date(2025, 10, 21), 3.1, 101.6, "MABIMS", 10, 28)]


def test_midnight_without_tomorrow_uses_today_fajr() -> None:
    now = datetime(2025, 10, 20, 20, 30, tzinfo=TZ)
    harness = _harness(rows=(_day(DAY),), calc=None, now=now)
    event = harness.engine.next_event(now)
    assert event.state is PrayerState.NORMAL
    assert event.next_prayer is MarkerName.FAJR
    assert event.adhan_at == datetime.combine(
        date(2025, 10, 21), time(5, 45), tzinfo=TZ
    )


def test_stale_day_passes_into_event() -> None:
    old = _day(date(2025, 10, 17), fetched_at=NOW - timedelta(hours=72))
    harness = _harness(rows=(old,), calc=None)
    event = harness.engine.next_event(NOW)
    assert event.stale is True


def test_missing_iqamah_rule_raises_config_error() -> None:
    harness = _harness(settings=_settings(iqamah_rules=()), rows=(_day(DAY),))
    with pytest.raises(ConfigError):
        harness.engine.next_event(NOW)


def test_schedule_error_propagates() -> None:
    harness = _harness(calc=None)
    with pytest.raises(ScheduleError):
        harness.engine.next_event(NOW)


# --- Task 4: tick (Clock-driven state/tick fan-out) -----------------------


def _tick_harness(now: datetime) -> Harness:
    """Fresh harness with a seeded fresh JAKIM day for 2025-10-20 SGR01."""
    return _harness(rows=(_day(DAY, fetched_at=now - timedelta(hours=1)),), now=now)


def test_first_tick_publishes_state_then_tick() -> None:
    now = datetime(2025, 10, 20, 12, 9, tzinfo=TZ)
    harness = _tick_harness(now)
    event = harness.engine.tick()
    assert harness.bus.events == ["state", "tick"]
    assert event.now == harness.clock.now()
    assert event.state is PrayerState.NORMAL


def test_repeat_tick_same_minute_silent() -> None:
    now = datetime(2025, 10, 20, 12, 9, tzinfo=TZ)
    harness = _tick_harness(now)
    harness.engine.tick()
    harness.engine.tick()
    assert harness.bus.events == ["state", "tick"]


def test_minute_rollover_publishes_tick_only() -> None:
    now = datetime(2025, 10, 20, 12, 12, tzinfo=TZ)
    harness = _tick_harness(now)
    first = harness.engine.tick()
    assert first.state is PrayerState.PRE_ADHAN
    harness.bus.events.clear()
    harness.clock.current = datetime(2025, 10, 20, 12, 13, tzinfo=TZ)
    harness.engine.tick()
    assert harness.bus.events == ["tick"]


def test_state_transition_publishes_state_before_tick() -> None:
    now = datetime(2025, 10, 20, 12, 9, tzinfo=TZ)
    harness = _tick_harness(now)
    harness.engine.tick()
    harness.bus.events.clear()
    # PRE_ADHAN starts exactly at adhan_at - PRE_ADHAN_WINDOW (12:15 - 5m).
    harness.clock.current = datetime(2025, 10, 20, 12, 10, tzinfo=TZ)
    event = harness.engine.tick()
    assert event.state is PrayerState.PRE_ADHAN
    assert harness.bus.events == ["state", "tick"]


def test_target_change_same_minute_publishes_state_only() -> None:
    now = datetime(2025, 10, 20, 12, 14, tzinfo=TZ)
    harness = _tick_harness(now)
    first = harness.engine.tick()
    assert first.iqamah_at == datetime(2025, 10, 20, 12, 25, tzinfo=TZ)
    harness.bus.events.clear()
    # Admin edits land live: Dhuhr delay 10 -> 5 moves the target to 12:20.
    harness.settings.settings = replace(
        harness.settings.settings, iqamah_rules=_rules(5)
    )
    second = harness.engine.tick()
    assert second.iqamah_at == datetime(2025, 10, 20, 12, 20, tzinfo=TZ)
    assert harness.bus.events == ["state"]


def test_boundary_optin_toggle_publishes_state_same_minute() -> None:
    now = datetime(2025, 10, 20, 12, 14, tzinfo=TZ)
    harness = _tick_harness(now)
    first = harness.engine.tick()
    assert first.next_boundary is None  # opt-in off: no pointer
    harness.bus.events.clear()
    # The installation opts in live: the pointer appears (PRD FR-1.7), so
    # the fingerprint must notice even though state/prayer targets don't.
    harness.settings.settings = replace(
        harness.settings.settings, boundary_countdown=True
    )
    second = harness.engine.tick()
    assert second.next_boundary is MarkerName.IMSAK
    assert harness.bus.events == ["state"]


def test_engine_never_publishes_config_update() -> None:
    harness = _tick_harness(datetime(2025, 10, 20, 12, 9, tzinfo=TZ))
    for minute in (9, 10, 12, 15, 18, 25, 45, 46):
        harness.clock.current = datetime(2025, 10, 20, 12, minute, tzinfo=TZ)
        harness.engine.tick()
    assert harness.bus.events
    assert set(harness.bus.events) <= {"state", "tick"}
    assert "config-update" not in harness.bus.events


def test_calc_receives_settings_offsets() -> None:
    calc = FakeCalc()
    harness = _harness(
        settings=_settings(lat=3.1, lon=101.6, imsak_offset_min=5, dhuha_offset_min=20),
        calc=calc,
    )
    harness.engine.resolve_day(date(2026, 9, 23), ZONE, harness.clock.now())
    assert calc.calls[0][4:] == (5, 20)
