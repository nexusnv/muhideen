"""Daily 02:00 JAKIM sync job + FR-1.1 retry chain (PRD.md:77).

One daily job (`jakim-sync`) fetches the configured zone's year and saves
every returned day; on `SyncError` (nothing was saved — keep-cache,
Decision 5) — including repository write failures converted at the
`run_sync` save boundary, so a locked/full database enters the same
retry chain instead of escaping the job — it schedules absolute retries
against the **injected clock**: 5 min, then 15 min, then 1 h — after the
third failed retry it gives up until the next 02:00 run. `ConfigError`
(first-boot setup incomplete) never retries: it logs and waits for the
next daily run. Every registered job sets `misfire_grace_time=None`
(+ `coalesce=True`): a late wake (GC pause, NTP step) still syncs —
skipping the run would start no retry chain — and catch-up pile-ups
collapse into one run.

Every instant comes from `Clock.now()` — no wall-time anywhere (the
purity gate). The scheduler is built but **not started** here: 1A-7 owns
starting it in a daemon thread (PRD.md:206 background sync never blocks
render; PRD.md:325 single process).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol, cast

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from muhideen.core.errors import ConfigError, SyncError
from muhideen.core.ports import Clock, JAKIMClient, PrayerRepo, SettingsRepo

logger = logging.getLogger(__name__)

# FR-1.1 (PRD.md:77): 5m / 15m / 1h after a failed sync.
RETRY_DELAYS_S: tuple[float, ...] = (300.0, 900.0, 3600.0)
MAX_RETRIES = 3


class _AddJob(Protocol):
    """Typed view of `BlockingScheduler.add_job`.

    APScheduler 3.x ships partial annotations and no `py.typed`, so the raw
    member is partially `Unknown` under strict pyright. Casting the
    *instance* (not the member) keeps both call sites fully type-checked
    without suppressing any diagnostic.
    """

    def add_job(
        self,
        func: Callable[..., object],
        trigger: BaseTrigger,
        *,
        id: str,
        replace_existing: bool,
        misfire_grace_time: int | None,
        coalesce: bool,
    ) -> object:
        """Register a job — implemented by the concrete scheduler."""
        ...


def _add_job(
    scheduler: BlockingScheduler,
    func: Callable[..., object],
    trigger: BaseTrigger,
    *,
    id: str,
) -> None:
    """Register a sync/retry job that never skips a due run.

    `misfire_grace_time=None` (APScheduler's default is 1 second) means a
    late wake still runs the job: both the daily sync and each absolute
    retry are idempotent, and a skipped retry would silently break the
    5m/15m/1h chain. `coalesce=True` collapses any pile-up of due runs
    into one execution.
    """
    cast(_AddJob, scheduler).add_job(
        func,
        trigger,
        id=id,
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )


def run_sync(
    *,
    client: JAKIMClient,
    prayer_repo: PrayerRepo,
    settings_repo: SettingsRepo,
    clock: Clock,
) -> int:
    """Fetch the configured zone's year and save every day; return the count.

    `ConfigError` from `settings_repo.load()` propagates to the caller —
    first-boot setup has nothing to sync and must never retry. A
    repository write failure during the save loop is converted to
    `SyncError` (with the original chained): `sync_job` only schedules
    retries for `SyncError`, so an uncaught backend error (locked/full
    database) would otherwise escape the job and skip the whole 5m/15m/1h
    chain. When `SyncError` propagates, days earlier in the loop have
    already been saved — a partial save the caller sees as a failed sync.
    Each `save_day` is an independent upsert, so the partial write is
    repaired by the next attempt, which rewrites the full year.
    """
    settings = settings_repo.load()
    days = client.fetch_year(settings.zone)
    for day in days:
        try:
            prayer_repo.save_day(day)
        except (ConfigError, SyncError):
            raise
        except Exception as exc:
            raise SyncError(
                f"prayer repo write failed: {exc}", zone=settings.zone
            ) from exc
    return len(days)


def sync_job(
    *,
    client: JAKIMClient,
    prayer_repo: PrayerRepo,
    settings_repo: SettingsRepo,
    clock: Clock,
    scheduler: BlockingScheduler,
    attempt: int = 0,
) -> int | None:
    """Run one sync attempt; schedule the next absolute retry on failure."""
    try:
        return run_sync(
            client=client,
            prayer_repo=prayer_repo,
            settings_repo=settings_repo,
            clock=clock,
        )
    except ConfigError as exc:
        logger.warning(
            "jakim sync skipped (setup incomplete): %s; no retry scheduled",
            exc,
        )
        return None
    except SyncError as exc:
        if attempt >= MAX_RETRIES:
            logger.warning(
                "jakim sync gave up after %d attempts (zone=%s): %s",
                attempt + 1,
                exc.zone,
                exc,
            )
            return None
        next_attempt = attempt + 1
        run_at = clock.now() + timedelta(seconds=RETRY_DELAYS_S[attempt])
        _add_job(
            scheduler,
            functools.partial(
                sync_job,
                client=client,
                prayer_repo=prayer_repo,
                settings_repo=settings_repo,
                clock=clock,
                scheduler=scheduler,
                attempt=next_attempt,
            ),
            DateTrigger(run_date=run_at, timezone=clock.now().tzinfo),
            id=f"jakim-sync-retry-{next_attempt}",
        )
        logger.warning(
            "jakim sync failed (zone=%s, attempt=%d): %s",
            exc.zone,
            next_attempt,
            exc,
        )
        return None


def build_scheduler(
    *,
    client: JAKIMClient,
    prayer_repo: PrayerRepo,
    settings_repo: SettingsRepo,
    clock: Clock,
) -> BlockingScheduler:
    """Build the daily 02:00 scheduler with one `jakim-sync` cron job.

    Returns the scheduler **not started** — 1A-7 starts it in a daemon
    thread. `ValueError` if the clock is naive (Decision 7: every instant
    is tz-aware from the injected clock).
    """
    now = clock.now()
    tz = now.tzinfo
    if tz is None:
        raise ValueError("clock must be tz-aware")
    scheduler = BlockingScheduler(timezone=tz)
    _add_job(
        scheduler,
        functools.partial(
            sync_job,
            client=client,
            prayer_repo=prayer_repo,
            settings_repo=settings_repo,
            clock=clock,
            scheduler=scheduler,
            attempt=0,
        ),
        CronTrigger(hour=2, minute=0, timezone=tz),
        id="jakim-sync",
    )
    return scheduler
