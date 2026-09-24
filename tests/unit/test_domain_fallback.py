"""Fallback chain guards (slice 1A-2, Task 2)."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.errors import ScheduleError
from muhideen.core.values import PrayerDay, ScheduleSource

TZ = ZoneInfo("Asia/Kuala_Lumpur")


class FakeClock:
    """File-local pinned clock; production wires adapters/system_clock.py."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 1234.5


def _day(
    day: date = date(2025, 10, 20),
    zone: str = "SGR01",
    source: ScheduleSource = ScheduleSource.JAKIM,
    fetched_at: datetime | None = None,
) -> PrayerDay:
    from datetime import time as dtime

    return PrayerDay(
        date=day,
        zone=zone,
        imsak=dtime(5, 35),
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuha=dtime(7, 25),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=source,
        fetched_at=fetched_at or datetime(2025, 10, 20, 1, 0, tzinfo=TZ),
    )


def _now() -> datetime:
    return FakeClock(datetime(2025, 10, 20, 11, 45, tzinfo=TZ)).now()


@pytest.mark.unit
def test_cached_fresh_hit_not_stale() -> None:
    from muhideen.domain.fallback import resolve_day

    result = resolve_day(date(2025, 10, 20), "SGR01", _now(), _day(), None, None)
    assert result.day.zone == "SGR01"
    assert result.stale is False


@pytest.mark.unit
def test_cached_old_marks_stale() -> None:
    from muhideen.domain.fallback import resolve_day

    old = _day(fetched_at=datetime(2025, 10, 17, 10, 0, tzinfo=TZ))
    result = resolve_day(date(2025, 10, 20), "SGR01", _now(), old, None, None)
    assert result.day == old
    assert result.stale is True


@pytest.mark.unit
def test_calc_fallback_marks_stale() -> None:
    from muhideen.domain.fallback import resolve_day

    calc = _day(source=ScheduleSource.CALC)
    result = resolve_day(date(2025, 10, 20), "SGR01", _now(), None, calc, None)
    assert result.day.source == ScheduleSource.CALC
    assert result.stale is True


@pytest.mark.unit
def test_last_known_marks_stale() -> None:
    from muhideen.domain.fallback import resolve_day

    last = _day(day=date(2025, 10, 19))
    result = resolve_day(date(2025, 10, 20), "SGR01", _now(), None, None, last)
    assert result.day.date == date(2025, 10, 19)
    assert result.stale is True


@pytest.mark.unit
def test_all_miss_raises_schedule_error() -> None:
    from muhideen.domain.fallback import resolve_day

    with pytest.raises(ScheduleError) as exc:
        resolve_day(date(2025, 10, 20), "SGR01", _now(), None, None, None)
    assert exc.value.zone == "SGR01"
    assert exc.value.date == "2025-10-20"


@pytest.mark.unit
def test_zone_mismatch_ignored() -> None:
    from muhideen.domain.fallback import resolve_day

    wrong_zone = _day(zone="WKP01")
    calc = _day(source=ScheduleSource.CALC)
    result = resolve_day(date(2025, 10, 20), "SGR01", _now(), wrong_zone, calc, None)
    assert result.day.source == ScheduleSource.CALC


@pytest.mark.unit
def test_is_stale_48h_boundary() -> None:
    from muhideen.domain.fallback import is_stale

    now = _now()
    fresh_edge = _day(fetched_at=now - timedelta(hours=48))
    assert is_stale(fresh_edge, now) is False
    old_edge = _day(fetched_at=now - timedelta(hours=48, seconds=1))
    assert is_stale(old_edge, now) is True
    calc_fresh = _day(source=ScheduleSource.CALC, fetched_at=now)
    assert is_stale(calc_fresh, now) is True


@pytest.mark.unit
def test_fallback_result_frozen() -> None:
    from muhideen.domain.fallback import FallbackResult

    result = FallbackResult(day=_day(), stale=False)
    with pytest.raises(FrozenInstanceError):
        result.stale = True  # type: ignore[misc]


@pytest.mark.unit
def test_merge_days_prefers_cached_markers() -> None:
    from datetime import time

    from muhideen.domain.fallback import merge_days

    cached = PrayerDay(
        date=date(2026, 9, 23),
        zone="SGR01",
        imsak=time(5, 45),
        fajr=time(5, 55),
        syuruq=time(7, 1),
        dhuha=time(7, 26),
        dhuhr=time(13, 9),
        asr=time(16, 14),
        maghrib=time(19, 11),
        isha=time(20, 20),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2026, 9, 23, 1, 0, tzinfo=TZ),
    )
    calc = PrayerDay(
        date=date(2026, 9, 23),
        zone="SGR01",
        imsak=time(5, 40),
        fajr=time(5, 50),
        syuruq=time(7, 0),
        dhuha=time(7, 30),
        dhuhr=time(12, 20),
        asr=time(15, 35),
        maghrib=time(18, 10),
        isha=time(19, 30),
        source=ScheduleSource.CALC,
        fetched_at=datetime(2026, 9, 23, 1, 0, tzinfo=TZ),
    )
    merged = merge_days(cached, calc)
    assert merged is not None
    assert (merged.fajr, merged.dhuhr, merged.source) == (
        time(5, 55),
        time(13, 9),
        ScheduleSource.JAKIM,
    )


@pytest.mark.unit
def test_merge_days_falls_back_to_calc() -> None:
    from datetime import time

    from muhideen.domain.fallback import merge_days

    calc = PrayerDay(
        date=date(2026, 9, 23),
        zone="SGR01",
        imsak=time(5, 40),
        fajr=time(5, 50),
        syuruq=time(7, 0),
        dhuha=time(7, 30),
        dhuhr=time(12, 20),
        asr=time(15, 35),
        maghrib=time(18, 10),
        isha=time(19, 30),
        source=ScheduleSource.CALC,
        fetched_at=datetime(2026, 9, 23, 1, 0, tzinfo=TZ),
    )
    assert merge_days(None, calc) == calc
    assert merge_days(None, None) is None
