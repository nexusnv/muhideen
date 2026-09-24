"""Chronological ordering invariant for a day's eight markers (PRD §6.1).

One pure validator shared by both schedule sources: parsed JAKIM rows and
calc-produced days must satisfy ``Imsak <= Fajr < Syuruq < Dhuha < Dhuhr
< Asr < Maghrib < Isha``. Equality on the first pair only is the imsak
disabled signal (calc ``imsak_offset_min=0`` yields ``imsak == fajr``;
JAKIM rows are strict in practice). The repo still stores whatever it is
given; rejection happens at the source adapters, before any write.
"""

from muhideen.core.errors import SyncError
from muhideen.core.values import MarkerName, PrayerDay

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
    """Return ``day`` when ``imsak <= fajr`` and later markers increase.

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
        ok = left_value <= right_value if index == 0 else left_value < right_value
        if not ok:
            raise SyncError(
                f"time order violated: {left_name.value} {left_value} "
                f"!<{'' if index else '='} {right_name.value} {right_value}",
                zone=day.zone,
                date=day.date.isoformat(),
            )
    return day
