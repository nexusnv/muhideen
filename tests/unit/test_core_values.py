"""Guards for core value objects (slice 1A-1, Task 1)."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, time

import pytest

from muhideen.core.values import (
    IqamahRule,
    NextEvent,
    PrayerDay,
    PrayerName,
    PrayerState,
    ScheduleSource,
    Settings,
)


def _day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 20),
        zone="SGR01",
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 19, 2, 0),
    )


def _event() -> NextEvent:
    return NextEvent(
        now=datetime(2025, 10, 20, 11, 45),
        state=PrayerState.IQAMAH_COUNTDOWN,
        next_prayer=PrayerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15),
        iqamah_at=datetime(2025, 10, 20, 12, 30),
        dim_until=datetime(2025, 10, 20, 12, 50),
        stale=False,
    )


@pytest.mark.unit
def test_prayer_day_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _day().zone = "WKP01"  # type: ignore[misc]


@pytest.mark.unit
def test_next_event_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _event().stale = True  # type: ignore[misc]


@pytest.mark.unit
def test_iqamah_rule_and_settings_frozen() -> None:
    rule = IqamahRule(prayer=PrayerName.FAJR, mode="delay", delay_minutes=15)
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    with pytest.raises(FrozenInstanceError):
        rule.delay_minutes = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.zone = "WKP01"  # type: ignore[misc]


@pytest.mark.unit
def test_value_equality_and_hash() -> None:
    assert _day() == _day()
    assert hash(_day()) == hash(_day())
    other = PrayerDay(
        date=date(2025, 10, 20),
        zone="WKP01",
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 19, 2, 0),
    )
    assert _day() != other


@pytest.mark.unit
def test_next_event_equality_and_hash() -> None:
    assert _event() == _event()
    assert hash(_event()) == hash(_event())
    other = NextEvent(
        now=datetime(2025, 10, 20, 11, 45),
        state=PrayerState.NORMAL,
        next_prayer=PrayerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15),
        iqamah_at=datetime(2025, 10, 20, 12, 30),
        dim_until=datetime(2025, 10, 20, 12, 50),
        stale=False,
    )
    assert _event() != other


@pytest.mark.unit
def test_iqamah_rule_equality_and_hash() -> None:
    rule = IqamahRule(prayer=PrayerName.FAJR, mode="delay", delay_minutes=15)
    twin = IqamahRule(prayer=PrayerName.FAJR, mode="delay", delay_minutes=15)
    other = IqamahRule(prayer=PrayerName.FAJR, mode="delay", delay_minutes=20)
    assert rule == twin
    assert hash(rule) == hash(twin)
    assert rule != other


@pytest.mark.unit
def test_settings_equality_and_hash() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    twin = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    other = Settings(masjid_name="M", zone="WKP01", hijri_offset=0)
    assert settings == twin
    assert hash(settings) == hash(twin)
    assert settings != other


@pytest.mark.unit
@pytest.mark.parametrize("offset", [-3, -2, -1, 0, 1, 2, 3])
def test_settings_hijri_offset_range(offset: int) -> None:
    if -2 <= offset <= 2:
        assert Settings(masjid_name="M", zone="SGR01", hijri_offset=offset)
    else:
        with pytest.raises(ValueError, match="hijri_offset out of range"):
            Settings(masjid_name="M", zone="SGR01", hijri_offset=offset)


@pytest.mark.unit
def test_prayer_state_members() -> None:
    assert {s.name for s in PrayerState} == {
        "NORMAL",
        "PRE_ADHAN",
        "ADHAN",
        "IQAMAH_COUNTDOWN",
        "SALAH_DIM",
    }


@pytest.mark.unit
def test_prayer_name_members() -> None:
    assert {p.name.lower() for p in PrayerName} == {
        "fajr",
        "syuruq",
        "dhuhr",
        "asr",
        "maghrib",
        "isha",
        "jumuah",
    }


@pytest.mark.unit
def test_schedule_source_members() -> None:
    assert {s.value for s in ScheduleSource} == {"jakim", "calc", "manual"}
