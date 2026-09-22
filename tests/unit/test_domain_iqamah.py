"""Domain iqamah resolution guards (slice 1A-2, Task 1)."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.errors import ConfigError
from muhideen.core.values import IqamahRule, PrayerName

TZ = ZoneInfo("Asia/Kuala_Lumpur")


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


def _adhan() -> datetime:
    return datetime(2025, 10, 20, 12, 15, tzinfo=TZ)


@pytest.mark.unit
def test_delay_adds_minutes() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    assert resolve_iqamah(PrayerName.DHUHR, _adhan(), _rules()) == datetime(
        2025, 10, 20, 12, 25, tzinfo=TZ
    )


@pytest.mark.unit
def test_fixed_uses_clock_time() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    rules[PrayerName.ISHA] = IqamahRule(
        prayer=PrayerName.ISHA, mode="fixed", fixed_time=time(20, 30)
    )
    adhan = datetime(2025, 10, 20, 19, 25, tzinfo=TZ)
    assert resolve_iqamah(PrayerName.ISHA, adhan, rules) == datetime(
        2025, 10, 20, 20, 30, tzinfo=TZ
    )


@pytest.mark.unit
def test_syuruq_returns_none() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    adhan = datetime(2025, 10, 20, 6, 55, tzinfo=TZ)
    assert resolve_iqamah(PrayerName.SYURUQ, adhan, _rules()) is None


@pytest.mark.unit
def test_missing_rule_raises_config_error() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    del rules[PrayerName.ASR]
    with pytest.raises(ConfigError):
        resolve_iqamah(PrayerName.ASR, _adhan(), rules)


@pytest.mark.unit
def test_fixed_without_time_raises_config_error() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    rules[PrayerName.MAGHRIB] = IqamahRule(prayer=PrayerName.MAGHRIB, mode="fixed")
    adhan = datetime(2025, 10, 20, 18, 5, tzinfo=TZ)
    with pytest.raises(ConfigError):
        resolve_iqamah(PrayerName.MAGHRIB, adhan, rules)
