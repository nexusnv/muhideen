"""TIME UNSYNCED: NTP probe adapter + engine drift latch (FR-1.6, slice 1A-8)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from muhideen.adapters.time_sync import SystemTimeSyncProbe
from muhideen.api.dto import StateEventDTO
from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    PrayerDay,
    ScheduleSource,
    Settings,
)
from muhideen.engine import Engine

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")
NOW = datetime(2025, 10, 20, 12, 9, tzinfo=TZ)
DAY = date(2025, 10, 20)
ZONE = "SGR01"


class DriftingClock:
    """Wall + monotonic clock with independent control (step vs advance)."""

    def __init__(self, now: datetime) -> None:
        self._wall = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        """Normal passage: wall and monotonic move together."""
        self._wall += timedelta(seconds=seconds)
        self._mono += seconds

    def step_wall(self, seconds: float) -> None:
        """NTP/manual step: wall moves, monotonic does not."""
        self._wall += timedelta(seconds=seconds)


class StubProbe:
    """TimeSyncProbe double: fixed answer plus a call counter."""

    def __init__(self, synced: bool) -> None:
        self._synced = synced
        self.calls = 0

    def synchronized(self) -> bool:
        self.calls += 1
        return self._synced


class ScriptedRunner:
    """Subprocess-runner double: scripted stdout per binary, else missing."""

    def __init__(self, **scripted: str | Exception) -> None:
        self._scripted = scripted
        self.calls: list[str] = []

    def __call__(self, cmd: list[str]) -> str:
        binary = cmd[0]
        self.calls.append(binary)
        result = self._scripted.get(binary, FileNotFoundError(binary))
        if isinstance(result, Exception):
            raise result
        return result


class FakeSettingsRepo:
    """SettingsRepo double: fixed settings object."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> Settings:
        return self.settings

    def save(self, settings: Settings) -> None:
        self.settings = settings


class FakePrayerRepo:
    """PrayerRepo double: dict of seeded rows; no last-known fallback."""

    def __init__(self, rows: dict[tuple[date, str], PrayerDay]) -> None:
        self.rows = rows

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        return self.rows.get((day, zone))

    def save_day(self, prayer_day: PrayerDay) -> None:
        self.rows[(prayer_day.date, prayer_day.zone)] = prayer_day

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        return None


class RecordingBus:
    """Event bus double: records published event names in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def publish(self, event: str) -> None:
        self.events.append(event)


@dataclass
class Harness:
    clock: DriftingClock
    bus: RecordingBus
    engine: Engine


def _settings() -> Settings:
    return Settings(
        masjid_name="Masjid Test",
        zone=ZONE,
        hijri_offset=0,
        iqamah_rules=DEFAULT_IQAMAH_RULES,
    )


def _day() -> PrayerDay:
    return PrayerDay(
        date=DAY,
        zone=ZONE,
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=NOW - timedelta(hours=1),
    )


def _harness(time_sync: StubProbe | None) -> Harness:
    clock = DriftingClock(NOW)
    bus = RecordingBus()
    engine = Engine(
        settings_repo=FakeSettingsRepo(_settings()),
        prayer_repo=FakePrayerRepo({(DAY, ZONE): _day()}),
        clock=clock,
        event_bus=bus,
        time_sync=time_sync,
    )
    return Harness(clock, bus, engine)


# --- SystemTimeSyncProbe ---------------------------------------------------


def test_probe_true_when_timedatectl_reports_yes() -> None:
    runner = ScriptedRunner(timedatectl="yes\n")
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is True
    assert runner.calls == ["timedatectl"]


def test_probe_false_when_timedatectl_reports_no() -> None:
    runner = ScriptedRunner(timedatectl="no\n")
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is False
    assert runner.calls == ["timedatectl"]


def test_probe_caches_within_ttl_and_reprobes_after_expiry() -> None:
    clock = DriftingClock(NOW)
    runner = ScriptedRunner(timedatectl="yes")
    probe = SystemTimeSyncProbe(clock=clock, runner=runner)
    assert probe.synchronized() is True
    clock.advance(29.0)
    assert probe.synchronized() is True
    assert runner.calls == ["timedatectl"]  # inside the 30s TTL: one call
    clock.advance(31.0)  # 60s since the cached read
    assert probe.synchronized() is True
    assert runner.calls == ["timedatectl", "timedatectl"]


def test_expired_cache_probes_once_under_concurrent_callers() -> None:
    # Round-3 review: post-expiry thundering herd. Callers race in from the
    # ticker, the HTTP threadpool and off-loop SSE frames — while one refresh
    # is in flight, every other caller must share it, not spawn its own
    # timedatectl/chronyc (each up to a 10s timeout) on the Pi.
    entered_first = threading.Event()
    entered_second = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def runner(cmd: list[str]) -> str:
        calls.append(cmd[0])
        (entered_first if len(calls) == 1 else entered_second).set()
        release.wait(10)  # hold the refresh open until the test releases it
        return "yes"

    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    results: list[bool] = []

    def call() -> None:
        results.append(probe.synchronized())

    first = threading.Thread(target=call)
    first.start()
    assert entered_first.wait(5)  # first caller is inside the refresh
    second = threading.Thread(target=call)
    second.start()
    # Unlocked: the cache is still unwritten, so the second caller enters the
    # runner too (event fires). Locked: it waits on the refresh lock instead
    # (event never fires) — either way, release next and assert one probe.
    entered_second.wait(1)
    release.set()
    first.join(10)
    second.join(10)
    assert results == [True, True]
    assert calls == ["timedatectl"]  # one shared subprocess, not two


def test_probe_falls_back_to_chrony_when_timedatectl_missing() -> None:
    runner = ScriptedRunner(
        chronyc="Reference ID    : 00000000 (0.0.0.0)\nLeap status     : Normal\n"
    )
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is True
    assert runner.calls == ["timedatectl", "chronyc"]


def test_probe_fails_closed_when_no_tool_reports_sync() -> None:
    # Neither time tool installed.
    runner = ScriptedRunner()
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is False
    assert runner.calls == ["timedatectl", "chronyc"]

    # timedatectl present but output is not a parseable yes/no; chrony missing.
    runner = ScriptedRunner(timedatectl="garbage")
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is False
    assert runner.calls == ["timedatectl", "chronyc"]

    # chronyc reports an unsynchronised leap status.
    runner = ScriptedRunner(
        timedatectl=FileNotFoundError("timedatectl"),
        chronyc="Leap status     : Not synchronised\n",
    )
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is False

    # chronyc succeeded but the output carries no leap status line.
    runner = ScriptedRunner(
        timedatectl=FileNotFoundError("timedatectl"),
        chronyc="Some other daemon chatter\n",
    )
    probe = SystemTimeSyncProbe(clock=DriftingClock(NOW), runner=runner)
    assert probe.synchronized() is False


# --- Engine stamp + drift latch -------------------------------------------


def test_engine_without_probe_reports_time_synced_true() -> None:
    harness = _harness(None)
    assert harness.engine.next_event(NOW).time_synced is True


def test_probe_false_stamps_next_event_and_state_frame() -> None:
    harness = _harness(StubProbe(False))
    event = harness.engine.next_event(NOW)
    assert event.time_synced is False
    frame = StateEventDTO.from_domain(event).model_dump(mode="json", exclude_none=True)
    assert frame["time_synced"] is False


def test_wall_step_latches_time_unsynced_despite_synced_probe() -> None:
    harness = _harness(StubProbe(True))
    assert harness.engine.next_event(NOW).time_synced is True  # baseline sample
    harness.clock.step_wall(4.0)  # sub-threshold jitter: no latch
    assert harness.engine.next_event(harness.clock.now()).time_synced is True
    harness.clock.step_wall(6.0)  # wall moved, monotonic did not: drift
    assert harness.engine.next_event(harness.clock.now()).time_synced is False


def test_latch_clears_after_300s_with_synced_probe() -> None:
    harness = _harness(StubProbe(True))
    harness.engine.next_event(NOW)
    harness.clock.step_wall(6.0)
    assert harness.engine.next_event(harness.clock.now()).time_synced is False
    harness.clock.advance(100.0)  # probe still synced, but <300s since the step
    assert harness.engine.next_event(harness.clock.now()).time_synced is False
    harness.clock.advance(201.0)  # 301s since the step + fresh synced: clear
    assert harness.engine.next_event(harness.clock.now()).time_synced is True


def test_time_synced_flip_republishes_state_same_minute() -> None:
    harness = _harness(StubProbe(True))
    first = harness.engine.tick()
    assert first.time_synced is True
    assert harness.bus.events == ["state", "tick"]
    harness.bus.events.clear()
    harness.clock.step_wall(6.0)  # 12:09 -> 12:09:06: same minute
    event = harness.engine.tick()
    assert event.time_synced is False
    assert harness.bus.events == ["state"]
