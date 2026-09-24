"""MABIMS calc engine guards (slice 1A-6, Task 4).

Pinned scope: **9 functions / 19 items** — golden vectors ×6 (each asserts
all 8 markers ≤ `GOLDEN_TOLERANCE_MIN`), exact `imsak = fajr − 10` ×6,
ordering, unknown-method `ValueError`, provenance, port, imsak-zero
disable, dhuha-offset honored, out-of-range offsets raise. The 6 vectors
come from the recorded source research; tolerance is 5 (fixed-28 default;
SGR01 dhuha +5 worst case); golden times load from the committed year
payloads (same provenance).
"""

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.integration

DATA = Path(__file__).resolve().parents[2] / "tests" / "data"
TZ = ZoneInfo("Asia/Kuala_Lumpur")
PINNED = datetime(2026, 9, 23, 12, 0, tzinfo=TZ)

GOLDEN_TOLERANCE_MIN = 5

# (zone, latitude, longitude, golden date-string, parsed date)
VECTORS = [
    ("SGR01", 3.0738, 101.5167, "01-Jan-2026", date(2026, 1, 1)),
    ("SGR01", 3.0738, 101.5167, "01-Jun-2026", date(2026, 6, 1)),
    ("SGR01", 3.0738, 101.5167, "23-Sep-2026", date(2026, 9, 23)),
    ("KDH01", 6.1248, 100.3678, "01-Jan-2026", date(2026, 1, 1)),
    ("KDH01", 6.1248, 100.3678, "01-Jun-2026", date(2026, 6, 1)),
    ("KDH01", 6.1248, 100.3678, "01-Dis-2026", date(2026, 12, 1)),
]

# PrayerDay field names -> source row keys (only the syuruq spelling differs).
MARKERS = ("imsak", "fajr", "syuruq", "dhuha", "dhuhr", "asr", "maghrib", "isha")
_FIELD_TO_KEY = {"syuruq": "syuruk"}

_GOLDEN_CACHE: dict[str, Any] = {}


class FakeClock:
    """File-local pinned clock; production wires adapters/system_clock.py."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 1234.5


def _engine() -> Any:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    return MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)


def _compute(day: date, lat: float, lon: float, method: str = "MABIMS") -> PrayerDay:
    return _engine().compute_day(day, lat, lon, method)


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _golden(zone: str, date_text: str, marker: str) -> time:
    if zone not in _GOLDEN_CACHE:
        path = DATA / f"jakim_year_{zone.lower()}.json"
        _GOLDEN_CACHE[zone] = json.loads(path.read_text())
    rows = _GOLDEN_CACHE[zone]["prayerTime"]
    row = next(r for r in rows if r["date"] == date_text)
    value = row[_FIELD_TO_KEY.get(marker, marker)]
    return time(int(value[:2]), int(value[3:5]))


# --- golden fit ------------------------------------------------------------


@pytest.mark.parametrize(("zone", "lat", "lon", "date_text", "day"), VECTORS)
def test_fitted_golden_vectors_within_tolerance(
    zone: str, lat: float, lon: float, date_text: str, day: date
) -> None:
    computed = _compute(day, lat, lon)
    for marker in MARKERS:
        computed_min = _mins(getattr(computed, marker))
        golden_min = _mins(_golden(zone, date_text, marker))
        assert abs(computed_min - golden_min) <= GOLDEN_TOLERANCE_MIN, (
            f"{zone} {date_text} {marker}: {computed_min} vs {golden_min}"
        )


@pytest.mark.parametrize(("zone", "lat", "lon", "date_text", "day"), VECTORS)
def test_imsak_is_fajr_minus_10(
    zone: str, lat: float, lon: float, date_text: str, day: date
) -> None:
    del zone, date_text
    computed = _compute(day, lat, lon)
    assert _mins(computed.imsak) == _mins(computed.fajr) - 10


# --- invariants ------------------------------------------------------------


def test_compute_day_orders_markers_strictly() -> None:
    from muhideen.domain.ordering import ensure_ordered

    computed = _compute(date(2026, 9, 23), 3.0738, 101.5167)
    assert ensure_ordered(computed) is computed


def test_unknown_method_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported calculation method"):
        _compute(date(2026, 9, 23), 3.0738, 101.5167, method="nonexistent")


def test_compute_day_stamps_provenance() -> None:
    computed = _compute(date(2026, 9, 23), 3.0738, 101.5167)
    assert computed.source is ScheduleSource.CALC
    assert computed.fetched_at == PINNED
    assert computed.zone == ""  # the engine stamps the zone (engine.py)


def test_calc_engine_satisfies_port() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine
    from muhideen.core.ports import CalcEngine

    assert isinstance(MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ), CalcEngine)


def test_imsak_zero_disables_to_fajr() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    computed = engine.compute_day(
        date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", imsak_offset_min=0
    )
    assert computed.imsak == computed.fajr


def test_dhuha_offset_param_honored() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    computed = engine.compute_day(
        date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", dhuha_offset_min=15
    )
    assert (computed.dhuha.hour * 60 + computed.dhuha.minute) - (
        computed.syuruq.hour * 60 + computed.syuruq.minute
    ) == 15


def test_out_of_range_offsets_raise() -> None:
    import pytest

    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    with pytest.raises(ValueError):
        engine.compute_day(
            date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", imsak_offset_min=11
        )
    with pytest.raises(ValueError):
        engine.compute_day(
            date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", dhuha_offset_min=14
        )
