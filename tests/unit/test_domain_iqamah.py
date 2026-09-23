"""Domain iqamah resolution guards (slice 1A-2, Task 1)."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.errors import ConfigError
from muhideen.core.values import IqamahRule, MarkerName

TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _rules() -> dict[MarkerName, IqamahRule]:
    return {
        MarkerName.FAJR: IqamahRule(
            prayer=MarkerName.FAJR, mode="delay", delay_minutes=15
        ),
        MarkerName.DHUHR: IqamahRule(
            prayer=MarkerName.DHUHR, mode="delay", delay_minutes=10
        ),
        MarkerName.ASR: IqamahRule(
            prayer=MarkerName.ASR, mode="delay", delay_minutes=10
        ),
        MarkerName.MAGHRIB: IqamahRule(
            prayer=MarkerName.MAGHRIB, mode="delay", delay_minutes=10
        ),
        MarkerName.ISHA: IqamahRule(
            prayer=MarkerName.ISHA, mode="delay", delay_minutes=15
        ),
        MarkerName.JUMUAH: IqamahRule(
            prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=10
        ),
    }


def _adhan() -> datetime:
    return datetime(2025, 10, 20, 12, 15, tzinfo=TZ)


@pytest.mark.unit
def test_delay_adds_minutes() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    assert resolve_iqamah(MarkerName.DHUHR, _adhan(), _rules()) == datetime(
        2025, 10, 20, 12, 25, tzinfo=TZ
    )


@pytest.mark.unit
def test_fixed_uses_clock_time() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    rules[MarkerName.ISHA] = IqamahRule(
        prayer=MarkerName.ISHA, mode="fixed", fixed_time=time(20, 30)
    )
    adhan = datetime(2025, 10, 20, 19, 25, tzinfo=TZ)
    assert resolve_iqamah(MarkerName.ISHA, adhan, rules) == datetime(
        2025, 10, 20, 20, 30, tzinfo=TZ
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "marker", [MarkerName.IMSAK, MarkerName.SYURUQ, MarkerName.DHUHA]
)
def test_boundary_marker_raises_config_error(marker: MarkerName) -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    with pytest.raises(ConfigError, match="boundary"):
        resolve_iqamah(marker, _adhan(), _rules())


@pytest.mark.unit
def test_missing_rule_raises_config_error() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    del rules[MarkerName.ASR]
    with pytest.raises(ConfigError):
        resolve_iqamah(MarkerName.ASR, _adhan(), rules)


@pytest.mark.unit
def test_fixed_without_time_raises_config_error() -> None:
    from muhideen.domain.iqamah import resolve_iqamah

    rules = _rules()
    rules[MarkerName.MAGHRIB] = IqamahRule(prayer=MarkerName.MAGHRIB, mode="fixed")
    adhan = datetime(2025, 10, 20, 18, 5, tzinfo=TZ)
    with pytest.raises(ConfigError):
        resolve_iqamah(MarkerName.MAGHRIB, adhan, rules)
