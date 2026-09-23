"""02:00 scheduler + FR-1.1 retry chain guards (slice 1A-6, Task 5).

Pinned scope: 10 functions / 10 items (no parametrize). File-local doubles
per TESTING_STRATEGY.md:19; every instant comes from the injected pinned
clock (advance-by-assignment) — no wall-time anywhere.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.errors import ConfigError, SyncError
from muhideen.core.values import PrayerDay, ScheduleSource, Settings

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")
PINNED = datetime(2026, 9, 24, 1, 30, tzinfo=TZ)
LOGGER = "muhideen.adapters.scheduler"


class FakeClock:
    """File-local pinned clock; advance by assigning `.current`."""

    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return 1234.5


class FakeSettingsRepo:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.settings = Settings(
            masjid_name="Masjid Test", zone="SGR01", hijri_offset=0
        )

    def load(self) -> Settings:
        if self._error is not None:
            raise self._error
        return self.settings

    def save(self, settings: Settings) -> None:
        self.settings = settings


class FakePrayerRepo:
    def __init__(self, days: list[PrayerDay] | None = None) -> None:
        self.stored: list[PrayerDay] = list(days or [])
        self.save_calls: list[PrayerDay] = []

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        return next((d for d in self.stored if d.date == day and d.zone == zone), None)

    def save_day(self, prayer_day: PrayerDay) -> None:
        self.save_calls.append(prayer_day)
        self.stored.append(prayer_day)

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        candidates = [d for d in self.stored if d.date <= day and d.zone == zone]
        return max(candidates, key=lambda d: d.date, default=None)


class FakeJAKIMClient:
    def __init__(
        self,
        days: list[PrayerDay] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.days = list(days or [])
        self.error = error
        self.last_zone: str | None = None

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        self.last_zone = zone
        if self.error is not None:
            raise self.error
        return self.days


def _day(day: date, zone: str = "SGR01") -> PrayerDay:
    return PrayerDay(
        date=day,
        zone=zone,
        imsak=time(5, 45),
        fajr=time(5, 55),
        syuruq=time(7, 1),
        dhuha=time(7, 26),
        dhuhr=time(13, 9),
        asr=time(16, 14),
        maghrib=time(19, 11),
        isha=time(20, 20),
        source=ScheduleSource.JAKIM,
        fetched_at=PINNED,
    )


def _defaults() -> dict[str, Any]:
    return {
        "client": FakeJAKIMClient(),
        "prayer_repo": FakePrayerRepo(),
        "settings_repo": FakeSettingsRepo(),
        "clock": FakeClock(PINNED),
    }


def _build(**overrides: Any) -> Any:
    from muhideen.adapters.scheduler import build_scheduler

    kwargs = _defaults() | overrides
    return build_scheduler(**kwargs)


def _run_sync(**overrides: Any) -> int:
    from muhideen.adapters.scheduler import run_sync

    kwargs = _defaults() | overrides
    return run_sync(**kwargs)


def _sync_job(scheduler: Any, **overrides: Any) -> Any:
    from muhideen.adapters.scheduler import sync_job

    kwargs = _defaults() | overrides
    return sync_job(scheduler=scheduler, **kwargs)


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


# --- build -----------------------------------------------------------------


def test_build_scheduler_registers_daily_0200_cron() -> None:
    scheduler = _build()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "jakim-sync"
    assert str(job.trigger) == "cron[hour='2', minute='0']"
    assert job.trigger.timezone == TZ
    assert scheduler.running is False  # 1A-7 starts it; never here


def test_unaware_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        _build(clock=FakeClock(datetime(2026, 9, 24, 1, 30)))  # naive


# --- run_sync --------------------------------------------------------------


def test_run_sync_saves_every_day_for_the_configured_zone() -> None:
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24)), _day(date(2026, 9, 25))])
    repo = FakePrayerRepo()
    assert _run_sync(client=client, prayer_repo=repo) == 2
    assert client.last_zone == "SGR01"
    assert len(repo.save_calls) == 2
    assert [d.date for d in repo.save_calls] == [date(2026, 9, 24), date(2026, 9, 25)]


def test_run_sync_with_empty_year_returns_zero() -> None:
    repo = FakePrayerRepo()
    assert _run_sync(client=FakeJAKIMClient(days=[]), prayer_repo=repo) == 0
    assert repo.save_calls == []


def test_run_sync_before_setup_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        _run_sync(settings_repo=FakeSettingsRepo(error=ConfigError("settings missing")))


# --- retry chain -----------------------------------------------------------


def test_failure_keeps_cache_and_schedules_first_retry() -> None:
    repo = FakePrayerRepo(days=[_day(date(2026, 9, 23))])  # seeded cache row
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client, prayer_repo=repo, clock=clock)
    result = _sync_job(
        scheduler=scheduler,
        client=client,
        prayer_repo=repo,
        clock=clock,
    )
    assert result is None
    assert repo.save_calls == []  # keep-cache: seed row untouched
    retry1 = scheduler.get_job("jakim-sync-retry-1")
    assert retry1 is not None
    assert retry1.trigger.run_date == clock.now() + timedelta(seconds=300)


def test_chained_failures_schedule_15m_then_1h() -> None:
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client, clock=clock)
    assert _sync_job(scheduler=scheduler, client=client, clock=clock) is None

    scheduler.get_job("jakim-sync-retry-1").func()
    retry2 = scheduler.get_job("jakim-sync-retry-2")
    assert retry2 is not None
    assert retry2.trigger.run_date == clock.now() + timedelta(seconds=900)

    retry2.func()
    retry3 = scheduler.get_job("jakim-sync-retry-3")
    assert retry3 is not None
    assert retry3.trigger.run_date == clock.now() + timedelta(seconds=3600)


def test_gives_up_after_three_retries(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _sync_job(scheduler=scheduler, client=client)
        scheduler.get_job("jakim-sync-retry-1").func()
        scheduler.get_job("jakim-sync-retry-2").func()
        scheduler.get_job("jakim-sync-retry-3").func()
    assert scheduler.get_job("jakim-sync-retry-4") is None
    assert any("gave up" in r.getMessage() for r in _records(caplog))


def test_job_logs_config_error_without_retry(caplog: pytest.LogCaptureFixture) -> None:
    settings_repo = FakeSettingsRepo(error=ConfigError("settings missing"))
    scheduler = _build(settings_repo=settings_repo)
    cron = scheduler.get_job("jakim-sync")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert cron.func() is None
    assert scheduler.get_job("jakim-sync-retry-1") is None
    messages = [r.getMessage() for r in _records(caplog)]
    assert any("setup" in m and "settings missing" in m for m in messages)


def test_failure_logs_zone_and_attempt(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _sync_job(scheduler=scheduler, client=client)
    messages = [r.getMessage() for r in _records(caplog)]
    assert any("zone=SGR01" in m and "attempt=1" in m for m in messages)
