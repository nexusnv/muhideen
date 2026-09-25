"""Chronological ordering invariant for a day's eight markers (PRD §6.1).

One pure validator shared by both schedule sources: days must satisfy
``Imsak <= Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha``, where
equality on the first pair is allowed only for ``ScheduleSource.CALC``
(the imsak disabled signal when ``imsak_offset_min=0`` yields
``imsak == fajr``). JAKIM rows stay strict — they are strict in practice
and a degenerate equal pair must surface as a rejection, not a schedule.
The repo still stores whatever it is given (1A-5 contract); rejection
happens at the source adapters, before any write.
"""

from muhideen.core.errors import SyncError
from muhideen.core.values import MarkerName, PrayerDay, ScheduleSource

ORDER: tuple[MarkerName, ...] = (
    MarkerName.IMSAK,
    MarkerName.FAJR,
    MarkerName.SYURUQ,
    MarkerName.DHUHA,
    MarkerName.DHUHR,
    MarkerName.ASR,
    MarkerName.MAGHRIB,
    MarkerName.ISHA,
)
"""The 8 source markers in required chronological order (no ``JUMUAH``:
it replaces ``DHUHR`` on Friday at the rule layer, never a 9th slot)."""


def ensure_ordered(day: PrayerDay) -> PrayerDay:
    """Return ``day`` when ordering holds (``imsak == fajr`` only for CALC).

    The first violated adjacent pair raises ``SyncError`` carrying the
    zone and date context plus both offending markers, so adapters can
    reject + keep cache with an actionable log line.
    """
    slots = (
        (MarkerName.IMSAK, day.imsak),
        (MarkerName.FAJR, day.fajr),
        (MarkerName.SYURUQ, day.syuruq),
        (MarkerName.DHUHA, day.dhuha),
        (MarkerName.DHUHR, day.dhuhr),
        (MarkerName.ASR, day.asr),
        (MarkerName.MAGHRIB, day.maghrib),
        (MarkerName.ISHA, day.isha),
    )
    for index, ((left_name, left_value), (right_name, right_value)) in enumerate(
        zip(slots, slots[1:], strict=False)
    ):
        allow_equal = index == 0 and day.source is ScheduleSource.CALC
        ok = left_value <= right_value if allow_equal else left_value < right_value
        if not ok:
            raise SyncError(
                f"time order violated: {left_name.value} {left_value} "
                f"!<{'=' if allow_equal else ''} {right_name.value} {right_value}",
                zone=day.zone,
                date=day.date.isoformat(),
            )
    return day
