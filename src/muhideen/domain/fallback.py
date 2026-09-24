"""Fallback chain: cached schedule, then calculation, then last-known day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from muhideen.core.errors import ScheduleError
from muhideen.core.values import PrayerDay, ScheduleSource

STALE_AFTER = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class FallbackResult:
    """Resolved schedule plus whether it came from a degraded source."""

    day: PrayerDay
    stale: bool


def is_stale(day: PrayerDay, now: datetime) -> bool:
    """Flag schedules older than 48h or from a degraded source."""
    if (now - day.fetched_at) > STALE_AFTER:
        return True
    return day.source is not ScheduleSource.JAKIM


def resolve_day(
    requested: date,
    zone: str,
    now: datetime,
    cached: PrayerDay | None,
    calculated: PrayerDay | None,
    last_known: PrayerDay | None,
) -> FallbackResult:
    """Resolve one day schedule in FR-1.2 priority order."""
    if cached is not None and cached.date == requested and cached.zone == zone:
        return FallbackResult(day=cached, stale=is_stale(cached, now))
    if (
        calculated is not None
        and calculated.date == requested
        and calculated.zone == zone
    ):
        return FallbackResult(day=calculated, stale=is_stale(calculated, now))
    if last_known is not None and last_known.zone == zone:
        return FallbackResult(day=last_known, stale=True)
    raise ScheduleError(
        f"no schedule for {requested.isoformat()} zone {zone}",
        zone,
        requested.isoformat(),
    )
