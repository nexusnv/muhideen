"""02:00 scheduler + FR-1.1 retry chain guards (slice 1A-6, Task 5).

Pinned scope: 11 functions / 11 items (no parametrize). File-local doubles
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
    def __init__(
        self,
        days: list[PrayerDay] | None = None,
        save_error: Exception | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self.stored: list[PrayerDay] = list(days or [])
        self.save_calls: list[PrayerDay] = []
        self._save_error = save_error
        self._read_error = read_error

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        if self._read_error is not None:
            raise self._read_error
        return next((d for d in self.stored if d.date == day and d.zone == zone), None)

    def save_day(self, prayer_day: PrayerDay) -> None:
        if self._save_error is not None:
            raise self._save_error
        self.save_calls.append(prayer_day)
        self.stored.append(prayer_day)

    def save_day_unless_manual(self, prayer_day: PrayerDay) -> bool:
        if self._read_error is not None:
            raise self._read_error
        if self._save_error is not None:
            raise self._save_error
        for index, day in enumerate(self.stored):
            if day.date == prayer_day.date and day.zone == prayer_day.zone:
                if day.source is ScheduleSource.MANUAL:
                    return False
                self.stored[index] = prayer_day
                self.save_calls.append(prayer_day)
                return True
        self.save_calls.append(prayer_day)
        self.stored.append(prayer_day)
        return True

    def delete_day(self, day: date, zone: str) -> bool:
        for index, stored in enumerate(self.stored):
            if (
                stored.date == day
                and stored.zone == zone
                and stored.source is ScheduleSource.MANUAL
            ):
                del self.stored[index]
                return True
        return False

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
    assert job.misfire_grace_time is None  # never skip a due run
    assert job.coalesce is True  # collapse catch-up pile-ups to one run
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


def test_repo_config_error_reraises_unwrapped() -> None:
    # Typed repo failures are not sync failures: ConfigError must reach the
    # caller as-is so the job logs it without scheduling a retry.
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    error = ConfigError("bad row")
    repo = FakePrayerRepo(save_error=error)
    with pytest.raises(ConfigError, match="bad row") as exc_info:
        _run_sync(client=client, prayer_repo=repo)
    assert exc_info.value is error


def test_repo_sync_error_reraises_unwrapped() -> None:
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    error = SyncError("stale lock", zone="SGR01", date="2026-09-24")
    repo = FakePrayerRepo(save_error=error)
    with pytest.raises(SyncError) as exc_info:
        _run_sync(client=client, prayer_repo=repo)
    assert exc_info.value is error


def test_repo_read_failure_converts_to_sync_error() -> None:
    # The stored-row read inside save_day_unless_manual sits inside the
    # same try as the write: an unexpected read failure (locked/full
    # database) becomes SyncError and enters the retry chain instead of
    # escaping sync_job.
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    repo = FakePrayerRepo(read_error=OSError("database is locked"))
    with pytest.raises(SyncError, match="prayer repo write failed"):
        _run_sync(client=client, prayer_repo=repo)


def test_repo_read_failure_schedules_retry() -> None:
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    repo = FakePrayerRepo(read_error=OSError("database is locked"))
    scheduler = _build(client=client, prayer_repo=repo, clock=clock)
    result = _sync_job(
        scheduler=scheduler,
        client=client,
        prayer_repo=repo,
        clock=clock,
    )
    assert result is None
    assert scheduler.get_job("jakim-sync-retry-1") is not None


def test_repo_write_failure_schedules_retry() -> None:
    # A repository failure (locked/full disk) must enter the retryable path:
    # an uncaught exception here would escape sync_job and skip the whole chain.
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    repo = FakePrayerRepo(save_error=OSError("disk full"))
    scheduler = _build(client=client, prayer_repo=repo, clock=clock)
    result = _sync_job(
        scheduler=scheduler,
        client=client,
        prayer_repo=repo,
        clock=clock,
    )
    assert result is None
    assert scheduler.get_job("jakim-sync-retry-1") is not None


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
    assert retry1.misfire_grace_time is None  # a late wake still retries
    assert retry1.coalesce is True


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
    # After 3 short retries the chain continues on a 6h long-pole instead
    # of going silent until the next 02:00 run.
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _sync_job(scheduler=scheduler, client=client)
        scheduler.get_job("jakim-sync-retry-1").func()
        scheduler.get_job("jakim-sync-retry-2").func()
        scheduler.get_job("jakim-sync-retry-3").func()
    assert scheduler.get_job("jakim-sync-retry-4") is None
    assert scheduler.get_job("jakim-sync-retry-long") is not None
    messages = [r.getMessage() for r in _records(caplog)]
    assert any("will retry in 6h" in m for m in messages)
    # initial run + 3 retries = 4 attempts total (attempt=3 must not under-report)
    assert any("after 4 attempts" in m for m in messages)


def test_non_transient_sync_error_skips_retry(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(
        error=SyncError("zone rejected", zone="BAD01", transient=False)
    )
    scheduler = _build(client=client, clock=clock)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert _sync_job(scheduler=scheduler, client=client, clock=clock) is None
    assert scheduler.get_job("jakim-sync-retry-1") is None
    assert scheduler.get_job("jakim-sync-retry-long") is None
    error_messages = [
        r.getMessage() for r in _records(caplog) if r.levelno >= logging.ERROR
    ]
    assert any("BAD01" in m and "check the zone" in m for m in error_messages)


def test_transient_failure_continues_long_pole_after_three_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock(PINNED)
    client = FakeJAKIMClient(error=SyncError("boom", zone="SGR01"))
    scheduler = _build(client=client, clock=clock)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _sync_job(scheduler=scheduler, client=client, clock=clock) is None
        scheduler.get_job("jakim-sync-retry-1").func()
        scheduler.get_job("jakim-sync-retry-2").func()
        scheduler.get_job("jakim-sync-retry-3").func()
    long_job = scheduler.get_job("jakim-sync-retry-long")
    assert long_job is not None
    assert long_job.trigger.run_date == clock.now() + timedelta(hours=6)
    assert long_job.misfire_grace_time is None  # a late wake still retries
    assert long_job.coalesce is True
    messages = [r.getMessage() for r in _records(caplog)]
    assert any("will retry in 6h" in m for m in messages)
    # re-arm: a further failure of the long-pole job re-schedules the same
    # stable id instead of going silent. The scheduler is never started in
    # tests, so re-adds accumulate as pending jobs (live startup collapses
    # them via `replace_existing`) — the last entry is the live re-arm.
    clock.current = PINNED + timedelta(hours=6, minutes=1)
    long_job.func()
    pending_long = [
        job for job in scheduler.get_jobs() if job.id == "jakim-sync-retry-long"
    ]
    assert len(pending_long) == 2
    rearmed = pending_long[-1]
    assert rearmed.trigger.run_date == clock.now() + timedelta(hours=6)


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


def test_calc_only_mode_skips_fetch() -> None:
    from dataclasses import replace

    settings_repo = FakeSettingsRepo()
    settings_repo.settings = replace(settings_repo.settings, calc_only=True)
    client = FakeJAKIMClient(days=[_day(date(2026, 9, 24))])
    repo = FakePrayerRepo()
    assert _run_sync(client=client, prayer_repo=repo, settings_repo=settings_repo) == 0
    assert client.last_zone is None
    assert repo.save_calls == []


def test_run_sync_skips_manually_pinned_days() -> None:
    from dataclasses import replace

    manual_date = date(2026, 9, 24)
    manual = replace(
        _day(manual_date),
        imsak=time(5, 40),
        fajr=time(5, 50),
        syuruq=time(7, 0),
        dhuha=time(7, 25),
        dhuhr=time(13, 5),
        asr=time(16, 10),
        maghrib=time(19, 8),
        isha=time(20, 15),
        source=ScheduleSource.MANUAL,
    )
    repo = FakePrayerRepo(days=[manual])
    client = FakeJAKIMClient(days=[_day(manual_date), _day(date(2026, 9, 25))])
    assert _run_sync(client=client, prayer_repo=repo) == 1
    assert [d.date for d in repo.save_calls] == [date(2026, 9, 25)]
    stored = repo.get_day(manual_date, "SGR01")
    assert stored == manual
    assert stored is not None and stored.source is ScheduleSource.MANUAL
    assert (stored.imsak, stored.fajr, stored.syuruq, stored.dhuha) == (
        manual.imsak,
        manual.fajr,
        manual.syuruq,
        manual.dhuha,
    )
    assert (stored.dhuhr, stored.asr, stored.maghrib, stored.isha) == (
        manual.dhuhr,
        manual.asr,
        manual.maghrib,
        manual.isha,
    )
    other = repo.get_day(date(2026, 9, 25), "SGR01")
    assert other is not None and other.source is ScheduleSource.JAKIM
