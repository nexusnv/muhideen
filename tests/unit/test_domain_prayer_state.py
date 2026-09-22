"""State machine core windows (slice 1A-2, Task 4)."""

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
        return 999.0


def _day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 22),
        zone="SGR01",
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 22, 1, 0, tzinfo=TZ),
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


def _at(hour: int, minute: int) -> datetime:
    return FakeClock(datetime(2025, 10, 22, hour, minute, tzinfo=TZ)).now()


@pytest.mark.unit
def test_normal_mid_morning() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(10, 0), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.NORMAL
    assert event.next_prayer == PrayerName.DHUHR
    assert event.adhan_at == datetime(2025, 10, 22, 12, 15, tzinfo=TZ)
    assert event.iqamah_at == datetime(2025, 10, 22, 12, 25, tzinfo=TZ)
    assert event.dim_until == datetime(2025, 10, 22, 12, 45, tzinfo=TZ)
    assert event.stale is False


@pytest.mark.unit
def test_pre_adhan_five_minute_window() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(12, 11), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.PRE_ADHAN
    assert event.next_prayer == PrayerName.DHUHR


@pytest.mark.unit
def test_adhan_overlay_uses_settings_duration() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(12, 16), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.ADHAN
    assert event.next_prayer == PrayerName.DHUHR
    assert event.adhan_at == datetime(2025, 10, 22, 12, 15, tzinfo=TZ)


@pytest.mark.unit
def test_iqamah_countdown_targets_rule() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(12, 20), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.IQAMAH_COUNTDOWN
    assert event.next_prayer == PrayerName.DHUHR
    assert event.iqamah_at == datetime(2025, 10, 22, 12, 25, tzinfo=TZ)
    assert event.dim_until == datetime(2025, 10, 22, 12, 45, tzinfo=TZ)


@pytest.mark.unit
def test_salah_dim_uses_default_minutes() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(12, 30), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.SALAH_DIM
    assert event.next_prayer == PrayerName.DHUHR
    assert event.dim_until == datetime(2025, 10, 22, 12, 45, tzinfo=TZ)


@pytest.mark.unit
def test_adhan_wins_when_overlay_overlaps_dim_window() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    rules = _rules()
    rules[PrayerName.DHUHR] = IqamahRule(
        prayer=PrayerName.DHUHR, mode="delay", delay_minutes=1
    )
    event = resolve_next_event(_at(12, 17), _day(), None, rules, _settings(), False)
    assert event.state == PrayerState.ADHAN
    assert event.next_prayer == PrayerName.DHUHR
    assert event.adhan_at == datetime(2025, 10, 22, 12, 15, tzinfo=TZ)
    assert event.iqamah_at == datetime(2025, 10, 22, 12, 16, tzinfo=TZ)
    assert event.dim_until == datetime(2025, 10, 22, 12, 36, tzinfo=TZ)


def _friday_day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 24),
        zone="SGR01",
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 24, 1, 0, tzinfo=TZ),
    )


def _friday_at(hour: int, minute: int) -> datetime:
    return FakeClock(datetime(2025, 10, 24, hour, minute, tzinfo=TZ)).now()


@pytest.mark.unit
def test_syuruq_overlay_then_normal_no_dim() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    overlay = resolve_next_event(_at(6, 56), _day(), None, _rules(), _settings(), False)
    assert overlay.state == PrayerState.ADHAN
    assert overlay.next_prayer == PrayerName.SYURUQ
    after = resolve_next_event(_at(7, 10), _day(), None, _rules(), _settings(), False)
    assert after.state == PrayerState.NORMAL
    assert after.next_prayer == PrayerName.DHUHR


@pytest.mark.unit
def test_syuruq_never_produces_iqamah_targets() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    overlay = resolve_next_event(_at(6, 56), _day(), None, _rules(), _settings(), False)
    assert overlay.iqamah_at is None
    assert overlay.dim_until is None


@pytest.mark.unit
def test_jumuah_replaces_dhuhr_friday() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(
        _friday_at(12, 20), _friday_day(), None, _rules(), _settings(), False
    )
    assert event.state == PrayerState.IQAMAH_COUNTDOWN
    assert event.next_prayer == PrayerName.JUMUAH
    assert event.adhan_at == datetime(2025, 10, 24, 12, 15, tzinfo=TZ)


@pytest.mark.unit
def test_jumuah_uses_45m_dim() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(
        _friday_at(12, 30), _friday_day(), None, _rules(), _settings(), False
    )
    assert event.state == PrayerState.SALAH_DIM
    assert event.next_prayer == PrayerName.JUMUAH
    assert event.dim_until == datetime(2025, 10, 24, 13, 10, tzinfo=TZ)


@pytest.mark.unit
def test_midnight_crossover_next_day_fajr() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(21, 0), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.NORMAL
    assert event.next_prayer == PrayerName.FAJR
    assert event.adhan_at == datetime(2025, 10, 23, 5, 45, tzinfo=TZ)


@pytest.mark.unit
def test_midnight_crossover_uses_tomorrow_schedule() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    tomorrow = PrayerDay(
        date=date(2025, 10, 23),
        zone="SGR01",
        fajr=dtime(5, 46),
        syuruq=dtime(6, 56),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 23, 1, 0, tzinfo=TZ),
    )
    event = resolve_next_event(
        _at(21, 0), _day(), tomorrow, _rules(), _settings(), False
    )
    assert event.next_prayer == PrayerName.FAJR
    assert event.adhan_at == datetime(2025, 10, 23, 5, 46, tzinfo=TZ)


@pytest.mark.unit
def test_syuruq_pre_adhan_window() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(6, 52), _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.PRE_ADHAN
    assert event.next_prayer == PrayerName.SYURUQ
    assert event.adhan_at == datetime(2025, 10, 22, 6, 55, tzinfo=TZ)
    assert event.iqamah_at is None
    assert event.dim_until is None


@pytest.mark.unit
def test_next_day_fajr_pre_adhan() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    now = FakeClock(datetime(2025, 10, 23, 5, 43, tzinfo=TZ)).now()
    event = resolve_next_event(now, _day(), None, _rules(), _settings(), False)
    assert event.state == PrayerState.PRE_ADHAN
    assert event.next_prayer == PrayerName.FAJR
    assert event.adhan_at == datetime(2025, 10, 23, 5, 45, tzinfo=TZ)


@pytest.mark.unit
def test_fixed_offset_tz_matches_zoneinfo() -> None:
    """Contract `+08:00` timestamps must behave like ZoneInfo equivalents."""

    from datetime import timedelta, timezone

    from muhideen.domain.prayer_state import resolve_next_event

    offset = timezone(timedelta(hours=8))
    fixed = datetime(2025, 10, 22, 12, 20, tzinfo=offset)
    zoned = datetime(2025, 10, 22, 12, 20, tzinfo=TZ)
    fixed_event = resolve_next_event(fixed, _day(), None, _rules(), _settings(), False)
    zoned_event = resolve_next_event(zoned, _day(), None, _rules(), _settings(), False)
    assert fixed_event.state == zoned_event.state == PrayerState.IQAMAH_COUNTDOWN
    assert fixed_event.next_prayer == zoned_event.next_prayer == PrayerName.DHUHR
    assert fixed_event.iqamah_at == zoned_event.iqamah_at


@pytest.mark.unit
def test_stale_day_remaps_onto_today() -> None:
    """A last-known day from another date renders as a template for today."""

    from muhideen.domain.prayer_state import resolve_next_event

    stale_day = PrayerDay(
        date=date(2025, 10, 22),
        zone="SGR01",
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.MANUAL,
        fetched_at=datetime(2025, 10, 20, 1, 0, tzinfo=TZ),
    )
    now = datetime(2025, 10, 23, 13, 0, tzinfo=TZ)
    event = resolve_next_event(now, stale_day, None, _rules(), _settings(), True)
    assert event.state == PrayerState.NORMAL
    assert event.next_prayer == PrayerName.ASR
    assert event.adhan_at is not None
    assert event.adhan_at > now
    assert event.stale is True


@pytest.mark.unit
def test_stale_flag_passes_through_true() -> None:
    from muhideen.domain.prayer_state import resolve_next_event

    event = resolve_next_event(_at(10, 0), _day(), None, _rules(), _settings(), True)
    assert event.state == PrayerState.NORMAL
    assert event.stale is True


@pytest.mark.unit
def test_friday_missing_jumuah_rule_raises_config_error() -> None:
    from muhideen.core.errors import ConfigError
    from muhideen.domain.prayer_state import resolve_next_event

    rules = _rules()
    del rules[PrayerName.JUMUAH]
    with pytest.raises(ConfigError):
        resolve_next_event(
            _friday_at(12, 20), _friday_day(), None, rules, _settings(), False
        )
