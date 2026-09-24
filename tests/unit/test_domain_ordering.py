"""Ordering validation guards (slice 1A-6, Task 2)."""

from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.errors import SyncError
from muhideen.core.values import PrayerDay, ScheduleSource

TZ = ZoneInfo("Asia/Kuala_Lumpur")

pytestmark = pytest.mark.unit

_ADJACENT_PAIRS = [
    ("imsak", "fajr"),
    ("fajr", "syuruq"),
    ("syuruq", "dhuha"),
    ("dhuha", "dhuhr"),
    ("dhuhr", "asr"),
    ("asr", "maghrib"),
    ("maghrib", "isha"),
]


def _day(
    day: date = date(2026, 9, 23),
    zone: str = "SGR01",
    source: ScheduleSource = ScheduleSource.JAKIM,
    fetched_at: datetime | None = None,
) -> PrayerDay:
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
        source=source,
        fetched_at=fetched_at or datetime(2026, 9, 23, 1, 0, tzinfo=TZ),
    )


def test_ordered_day_passes_through() -> None:
    from muhideen.domain.ordering import ensure_ordered

    day = _day()
    assert ensure_ordered(day) == day


@pytest.mark.parametrize(("left", "right"), _ADJACENT_PAIRS)
def test_every_adjacent_violation_raises_sync_error(left: str, right: str) -> None:
    from muhideen.domain.ordering import ensure_ordered

    base = _day()
    swapped = replace(
        base,
        **{left: getattr(base, right), right: getattr(base, left)},
    )
    with pytest.raises(SyncError):
        ensure_ordered(swapped)


def test_equal_adjacent_times_raise() -> None:
    from muhideen.domain.ordering import ensure_ordered

    with pytest.raises(SyncError):
        ensure_ordered(replace(_day(), syuruq=time(5, 55)))  # fajr == syuruq


def test_error_carries_zone_and_date_context() -> None:
    from muhideen.domain.ordering import ensure_ordered

    base = _day()
    swapped = replace(base, imsak=base.fajr, fajr=base.imsak)
    with pytest.raises(SyncError) as excinfo:
        ensure_ordered(swapped)
    exc = excinfo.value
    assert exc.zone == "SGR01"
    assert exc.date == "2026-09-23"
    assert "imsak" in str(exc)
    assert "fajr" in str(exc)


def test_imsak_equal_fajr_passes_as_disabled_signal() -> None:
    from muhideen.domain.ordering import ensure_ordered

    base = _day()
    disabled = replace(base, imsak=base.fajr)
    assert ensure_ordered(disabled) is disabled
