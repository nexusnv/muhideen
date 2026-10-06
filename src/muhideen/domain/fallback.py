"""Fallback chain: cached schedule, then calculation, then last-known day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from muhideen.core.errors import ScheduleError
from muhideen.core.values import PrayerDay, ScheduleSource

STALE_AFTER = timedelta(hours=48)

FRESH_SOURCES: frozenset[ScheduleSource] = frozenset(
    {ScheduleSource.JAKIM, ScheduleSource.ALADHAN}
)
"""Network-fetched sources: fresh rows are authoritative, never degraded."""


@dataclass(frozen=True, slots=True)
class FallbackResult:
    """Resolved schedule plus whether it came from a degraded source."""

    day: PrayerDay
    stale: bool


def is_stale(day: PrayerDay, now: datetime) -> bool:
    """Flag schedules older than 48h or from a degraded source."""
    fetched_at = day.fetched_at
    # Adapters have stored both naive and aware timestamps historically;
    # normalize naive as the same wall-time in now's zone (local time on
    # the Pi) rather than raising TypeError on the mix. A naive value is
    # assumed to be local wall-time, so attaching now's tzinfo preserves
    # the elapsed duration; the reverse mix strips the offset for the
    # same reason.
    if (fetched_at.tzinfo is None) != (now.tzinfo is None):
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=now.tzinfo)
        else:
            fetched_at = fetched_at.replace(tzinfo=None)
    if (now - fetched_at) > STALE_AFTER:
        return True
    return day.source not in FRESH_SOURCES


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


def merge_days(
    preferred: PrayerDay | None, fallback: PrayerDay | None
) -> PrayerDay | None:
    """Prefer ``preferred`` wholesale, else ``fallback``.

    Rows are complete today (the JAKIM parser rejects partial payloads),
    so a present ``preferred`` day wins wholesale — equivalent to a
    per-marker merge while rows stay complete. Per-marker pin completion
    lives in ``FilePrayerRepo._complete_pin`` (pin over provider row),
    not here. Provenance follows ``preferred``.
    """
    if preferred is None:
        return fallback
    if fallback is None:
        return preferred
    return PrayerDay(
        date=preferred.date,
        zone=preferred.zone,
        imsak=preferred.imsak,
        fajr=preferred.fajr,
        syuruq=preferred.syuruq,
        dhuha=preferred.dhuha,
        dhuhr=preferred.dhuhr,
        asr=preferred.asr,
        maghrib=preferred.maghrib,
        isha=preferred.isha,
        source=preferred.source,
        fetched_at=preferred.fetched_at,
    )
