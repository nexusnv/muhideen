"""Determinism proof (slice 1A-2, Task 6): same inputs, identical output."""

from datetime import date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.values import (
    IqamahRule,
    PrayerDay,
    PrayerName,
    PrayerState,
    ScheduleSource,
    Settings,
)

TZ = ZoneInfo("Asia/Kuala_Lumpur")


class FakeClock:
    """File-local pinned clock."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 7.0


def _snapshot() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 20),
        zone="SGR01",
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 20, 1, 0, tzinfo=TZ),
    )


def _rules() -> dict[PrayerName, IqamahRule]:
    return {
        PrayerName.FAJR: IqamahRule(
            prayer=PrayerName.FAJR, mode="delay", delay_minutes=15
        ),
        PrayerName.DHUHR: IqamahRule(
            prayer=PrayerName.DHUHR, mode="delay", delay_minutes=10
        ),
        PrayerName.ASR: IqamahRule(
            prayer=PrayerName.ASR, mode="delay", delay_minutes=10
        ),
        PrayerName.MAGHRIB: IqamahRule(
            prayer=PrayerName.MAGHRIB, mode="delay", delay_minutes=10
        ),
        PrayerName.ISHA: IqamahRule(
            prayer=PrayerName.ISHA, mode="delay", delay_minutes=15
        ),
        PrayerName.JUMUAH: IqamahRule(
            prayer=PrayerName.JUMUAH, mode="delay", delay_minutes=10
        ),
    }


def _settings() -> Settings:
    return Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        adhan_duration_s=180,
        dim_minutes_default=20,
        dim_minutes_jumuah=45,
    )


@pytest.mark.unit
def test_same_inputs_identical_output() -> None:
    from muhideen.domain import resolve_next_event

    now = FakeClock(datetime(2025, 10, 20, 12, 20, tzinfo=TZ)).now()
    first = resolve_next_event(now, _snapshot(), None, _rules(), _settings(), False)
    second = resolve_next_event(now, _snapshot(), None, _rules(), _settings(), False)
    assert first == second
    assert first.state == PrayerState.IQAMAH_COUNTDOWN
    assert first.next_prayer == PrayerName.DHUHR
    assert first.adhan_at == datetime(2025, 10, 20, 12, 15, tzinfo=TZ)
    assert first.iqamah_at == datetime(2025, 10, 20, 12, 25, tzinfo=TZ)
    assert first.dim_until == datetime(2025, 10, 20, 12, 45, tzinfo=TZ)
    assert first.stale is False
