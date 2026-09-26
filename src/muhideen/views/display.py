"""Display context builder: DTOs to Jinja context (frontend-owned).

Imports ``api.dto`` + ``core`` only — never ``domain``/``engine``/adapters.
"""

from __future__ import annotations

from muhideen.api.dto import NextEventDTO, PrayerDayDTO
from muhideen.core.values import ScheduleSource, Settings

PRAYER_ORDER = ("fajr", "dhuhr", "asr", "maghrib", "isha")

PRAYER_LABELS = {
    "fajr": ("Fajr", "Subuh", "الفجر"),
    "dhuhr": ("Dhuhr", "Zohor", "الظهر"),
    "asr": ("Asr", "Asar", "العصر"),
    "maghrib": ("Maghrib", "Maghrib", "المغرب"),
    "isha": ("Isha", "Isyak", "العشاء"),
    "jumuah": ("Jumuah", "Jumaat", "الجمعة"),
}

BOUNDARY_LABELS = {
    "imsak": ("Imsak", "Imsak", "الإمساك"),
    "syuruq": ("Syuruq", "Syuruk", "الشروق"),
    "dhuha": ("Dhuha", "Dhuha", "الضحى"),
}


def build_display_context(
    *, day: PrayerDayDTO, event: NextEventDTO, settings: Settings
) -> dict[str, object]:
    """Map resolved DTOs to the display template context (no time reads)."""
    times = {
        "fajr": day.prayers.fajr,
        "dhuhr": day.prayers.dhuhr,
        "asr": day.prayers.asr,
        "maghrib": day.prayers.maghrib,
        "isha": day.prayers.isha,
    }
    next_key = event.next_prayer or "fajr"
    labels = (
        PRAYER_LABELS["jumuah"] if next_key == "jumuah" else PRAYER_LABELS[next_key]
    )
    cards = [
        {
            "key": key,
            "en": PRAYER_LABELS[key][0],
            "bm": PRAYER_LABELS[key][1],
            "ar": PRAYER_LABELS[key][2],
            "time": times[key],
            "is_next": key == next_key or (key == "dhuhr" and next_key == "jumuah"),
        }
        for key in PRAYER_ORDER
    ]
    bounds: list[dict[str, object]] = []
    if settings.imsak_offset_min != 0:
        bounds.append({"key": "imsak", "time": day.boundaries.imsak})
    bounds.extend(
        [
            {"key": "syuruq", "time": day.boundaries.syuruq},
            {"key": "dhuha", "time": day.boundaries.dhuha},
        ]
    )
    for bound in bounds:
        key = str(bound["key"])
        bound["en"] = BOUNDARY_LABELS[key][0]
        bound["is_next"] = (
            event.next_boundary is not None and event.next_boundary == key
        )
    banners: list[str] = []
    if event.stale:
        banners.append("STALE — showing fallback schedule")
    if not event.time_synced:
        banners.append("TIME UNSYNCED")
    if day.source is not ScheduleSource.JAKIM:
        banners.append("CALC — computed schedule")
    return {
        "masjid_name": settings.masjid_name,
        "zone": settings.zone,
        "gregorian": day.date.isoformat(),
        "hijri": day.hijri_date or "—",
        "clock": event.now.strftime("%H:%M:%S"),
        "next_name_en": labels[0],
        "next_name_bm": labels[1],
        "next_name_ar": labels[2],
        "next_time": event.adhan_at.strftime("%H:%M") if event.adhan_at else "",
        "cards": cards,
        "bounds": bounds,
        "banners": banners,
        "next_boundary": event.next_boundary,
        "boundary_at": event.boundary_at.isoformat() if event.boundary_at else None,
    }
