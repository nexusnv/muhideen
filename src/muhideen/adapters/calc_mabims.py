"""On-device MABIMS prayer-time calculation (PRD FR-1.2's calc fallback).

al-falak's ``PrayerTimes`` surfaces 6 markers only (fajr, sunrise, dhuhr,
asr, maghrib, isha); ``imsak`` and ``dhuha`` are derived offsets, not
library outputs. ``sunrise`` maps to backend ``syuruq``.

Parameters are pinned by the recorded source research: fajr 17.75°,
isha 18.25°, Asr shadow factor 1 (Standard/MABIMS), dhuhr tune +2 min,
imsak = fajr − ``imsak_offset_min`` (default 10, 0 disables/hides),
dhuha = syuruq + ``dhuha_offset_min`` (default 28); fitted to JAKIM's
published tables with pooled max|e| = 5 minutes (`GOLDEN_TOLERANCE_MIN`,
asserted by the golden test). Computation library: al-falak 1.0.0 (MIT,
zero runtime deps) behind the `CalcEngine` port — swapping it is bounded
rework pinned by those golden tests. al-falak is the maintained fork of
the same batoulapps/adhan port lineage as adhanpy; library errors
(``AlFalakError`` subclasses) are translated to ``ValueError`` at this
boundary so the engine's cache-miss path is unchanged.

`zone` is left empty on purpose: the engine stamps it (`engine.py`
`replace(computed, zone=zone)`) because calc is zone-agnostic.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from alfalak import (
    AlFalakError,
    CalculationMethod,
    CalculationParameters,
    Madhab,
    PrayerAdjustments,
    PrayerTimes,
)

from muhideen.core.ports import Clock
from muhideen.core.values import AsrJuristic, PrayerDay, ScheduleSource
from muhideen.domain import ensure_ordered

FAJR_ANGLE_DEG = 17.75
ISHA_ANGLE_DEG = 18.25
DHUHR_TUNE_MIN = 2
DEFAULT_IMSAK_OFFSET_MIN = 10
DEFAULT_DHUHA_OFFSET_MIN = 28

# Only MABIMS parameters are fitted to JAKIM tables; the other FR-1.3
# contract methods use al-falak's built-in reference parameters.
# ISNA has no dedicated member: it is the North America method (15/15).
_METHODS: dict[str, CalculationMethod | None] = {
    "MABIMS": None,
    "MWL": CalculationMethod.MUSLIM_WORLD_LEAGUE,
    "ISNA": CalculationMethod.NORTH_AMERICA,
    "Egyptian": CalculationMethod.EGYPTIAN,
}

_SUPPORTED_METHODS = frozenset(_METHODS)


def _params(
    method: str = "MABIMS", madhab: Madhab = Madhab.SHAFI
) -> CalculationParameters:
    """Assemble the parameter set for one contract method.

    MABIMS keeps the fitted custom angles plus the dhuhr +2 tune (via
    ``adjustments``); built-in methods use their own reference angles and
    their own ``method_adjustments`` with no extra tune (both adjustment
    kinds are summed by the library, so adding ours would double-count).

    Raises ``ValueError`` for unknown method strings.
    """
    try:
        builtin = _METHODS[method]
    except KeyError:
        raise ValueError(f"unsupported calculation method: {method}") from None
    if builtin is None:
        params = CalculationParameters(
            fajr_angle=FAJR_ANGLE_DEG,
            isha_angle=ISHA_ANGLE_DEG,
            adjustments=PrayerAdjustments(dhuhr=DHUHR_TUNE_MIN),
        )
    else:
        params = CalculationParameters(method=builtin)
    params.madhab = madhab
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

    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = DEFAULT_IMSAK_OFFSET_MIN,
        dhuha_offset_min: int = DEFAULT_DHUHA_OFFSET_MIN,
        asr_juristic: AsrJuristic = "shafi",
    ) -> PrayerDay:
        """Compute one day's eight markers via al-falak; `ValueError` if unknown.

        Each contract method uses its own parameters: MABIMS keeps the
        fitted custom angles, MWL/ISNA/Egyptian use al-falak's built-in
        reference parameters (ISNA maps to North America). Truly unknown
        method strings are a cache miss (`ValueError`).

        al-falak supplies 6 markers (fajr, sunrise→syuruq, dhuhr, asr,
        maghrib, isha); imsak/dhuha are derived offsets
        (``imsak = fajr − imsak_offset_min`` with 0 meaning disabled/hidden,
        ``dhuha = syuruq + dhuha_offset_min``). Out-of-range offsets are a
        cache miss (`ValueError`), matching the Settings guards (0–10, 15–30).
        The result is stamped `ScheduleSource.CALC` with the pinned clock
        and passed through `ensure_ordered` before anything returns.
        """
        if method not in _SUPPORTED_METHODS:
            # engine.py converts ValueError to a cache miss, so an unknown
            # configured method degrades the chain instead of 500-ing.
            raise ValueError(f"unsupported calculation method: {method}")
        if not 0 <= imsak_offset_min <= 10:
            raise ValueError(f"imsak_offset_min out of range: {imsak_offset_min}")
        if not 15 <= dhuha_offset_min <= 30:
            raise ValueError(f"dhuha_offset_min out of range: {dhuha_offset_min}")
        try:
            madhab = {"shafi": Madhab.SHAFI, "hanafi": Madhab.HANAFI}[asr_juristic]
        except KeyError:
            raise ValueError(
                f"unknown asr juristic setting: {asr_juristic!r}"
            ) from None
        try:
            times = PrayerTimes(
                (lat, lon),
                datetime(day.year, day.month, day.day),
                calculation_parameters=_params(method, madhab),
                time_zone=self._tz,
            )
        except AlFalakError as exc:
            # The engine treats ValueError as a cache miss: a broken
            # calculator degrades the chain instead of 500-ing.
            raise ValueError(str(exc)) from None
        fajr: time = times.fajr.time()
        syuruq: time = times.sunrise.time()
        imsak = (
            datetime.combine(day, fajr) - timedelta(minutes=imsak_offset_min)
        ).time()
        dhuha = (
            datetime.combine(day, syuruq) + timedelta(minutes=dhuha_offset_min)
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
