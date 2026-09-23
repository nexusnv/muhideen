"""On-device MABIMS prayer-time calculation (PRD FR-1.2's calc fallback).

Parameters are pinned by the recorded source research: fajr 17.75°,
isha 18.25°, Asr shadow factor 1 (Standard/MABIMS), dhuhr tune +2 min,
imsak = fajr − 10 min, dhuha = syuruk + round(22.9851 + 0.6555 × lat);
fitted to JAKIM's published tables with pooled max|e| = 3 minutes
(`GOLDEN_TOLERANCE_MIN`, asserted by the golden test). Computation
library: adhanpy 1.0.5 (MIT, zero runtime deps) behind the `CalcEngine`
port — swapping it is bounded rework pinned by those golden tests.

`zone` is left empty on purpose: the engine stamps it (`engine.py`
`replace(computed, zone=zone)`) because calc is zone-agnostic.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from adhanpy.calculation.CalculationParameters import CalculationParameters
from adhanpy.calculation.Madhab import Madhab
from adhanpy.calculation.PrayerAdjustments import PrayerAdjustments
from adhanpy.PrayerTimes import PrayerTimes

from muhideen.core.ports import Clock
from muhideen.core.values import PrayerDay, ScheduleSource
from muhideen.domain import ensure_ordered

FAJR_ANGLE_DEG = 17.75
ISHA_ANGLE_DEG = 18.25
DHUHR_TUNE_MIN = 2
IMSAK_MINUS_FAJR_MIN = 10

# Only this slice's pinned method exists; other FR-1.3 methods (MWL, ISNA,
# Egyptian) are future work and fall back to MABIMS parameters today.
_PINNED_METHOD = "MABIMS"


def _dhuha_offset_min(lat: float) -> int:
    """Observed JAKIM rule: +25 min at lat 3.0738, +27 min at lat 6.1248."""
    return round(22.9851 + 0.6555 * lat)


def _params() -> CalculationParameters:
    """Assemble the pinned MABIMS parameter set (angles, asr factor, tunes)."""
    params = CalculationParameters(
        fajr_angle=FAJR_ANGLE_DEG,
        isha_angle=ISHA_ANGLE_DEG,
        adjustments=PrayerAdjustments(dhuhr=DHUHR_TUNE_MIN),
    )
    params.madhab = Madhab.SHAFI  # Asr shadow factor 1 (Standard / MABIMS)
    return params


class MabimsCalcEngine:
    """`CalcEngine` port implementation over MABIMS parameters (FR-1.2(2)).

    `tz` is the injected IANA `ZoneInfo` (production wires the display's
    zone, tests wire a fixed one) — no wall-clock reads, every instant
    comes from the injected clock.
    """

    def __init__(self, *, clock: Clock, tz: ZoneInfo) -> None:
        """Take the pinned clock and the injected display timezone."""
        self._clock = clock
        self._tz = tz

    def compute_day(self, day: date, lat: float, lon: float, method: str) -> PrayerDay:
        """Compute one day's eight markers via adhanpy; `ValueError` if unsupported.

        The result is stamped `ScheduleSource.CALC` with the pinned clock
        and passed through `ensure_ordered` before anything returns.
        """
        if method != _PINNED_METHOD:
            # engine.py converts ValueError to a cache miss, so an unknown
            # configured method degrades the chain instead of 500-ing.
            raise ValueError(f"unsupported calculation method: {method}")
        times = PrayerTimes(
            (lat, lon),
            datetime(day.year, day.month, day.day),
            calculation_parameters=_params(),
            time_zone=self._tz,
        )
        fajr: time = times.fajr.time()
        syuruq: time = times.sunrise.time()
        imsak = (
            datetime.combine(day, fajr) - timedelta(minutes=IMSAK_MINUS_FAJR_MIN)
        ).time()
        dhuha = (
            datetime.combine(day, syuruq) + timedelta(minutes=_dhuha_offset_min(lat))
        ).time()
        prayer_day = PrayerDay(
            date=day,
            zone="",
            imsak=imsak,
            fajr=fajr,
            syuruq=syuruq,
            dhuha=dhuha,
            dhuhr=times.dhuhr.time(),
            asr=times.asr.time(),
            maghrib=times.maghrib.time(),
            isha=times.isha.time(),
            source=ScheduleSource.CALC,
            fetched_at=self._clock.now(),
        )
        return ensure_ordered(prayer_day)
