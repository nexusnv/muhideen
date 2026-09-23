"""Chronological ordering invariant for a day's eight markers (PRD §6.1).

One pure validator shared by both schedule sources: parsed JAKIM rows and
calc-produced days must satisfy the same strict chain
``Imsak < Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha``
(``PRD.md`` §6.1). The repo still stores whatever it is given (1A-5
contract); rejection happens at the source adapters, before any write.
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
    """Return ``day`` unchanged when all eight markers are strictly increasing.

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
    for (left_name, left_value), (right_name, right_value) in zip(
        slots, slots[1:], strict=False
    ):
        if not left_value < right_value:
            raise SyncError(
                f"time order violated: {left_name.value} {left_value} "
                f"!< {right_name.value} {right_value}",
                zone=day.zone,
                date=day.date.isoformat(),
            )
    return day
