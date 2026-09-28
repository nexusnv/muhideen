"""Display context builder: DTOs to Jinja context (frontend-owned).

Imports ``api.dto`` + ``core`` only — never ``domain``/``engine``/adapters.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from muhideen.api.dto import NextEventDTO, PrayerDayDTO
from muhideen.core.errors import ConfigError
from muhideen.core.values import IqamahRule, ScheduleSource, Settings

PRAYER_ORDER = ("fajr", "dhuhr", "asr", "maghrib", "isha")

PRAYER_LABELS = {
    "fajr": ("Fajr", "الفجر"),
    "dhuhr": ("Dhuhr", "الظهر"),
    "asr": ("Asr", "العصر"),
    "maghrib": ("Maghrib", "المغرب"),
    "isha": ("Isha", "العشاء"),
    "jumuah": ("Jumuah", "الجمعة"),
}

BOUNDARY_LABELS = {
    "imsak": ("Imsak", "الإمساك"),
    "syuruq": ("Syuruq", "الشروق"),
    "dhuha": ("Dhuha", "الضحى"),
}

HIJRI_MONTHS = (
    "Muharram",
    "Safar",
    "Rabi' al-Awwal",
    "Rabi' al-Thani",
    "Jumada al-Ula",
    "Jumada al-Akhirah",
    "Rajab",
    "Sha'ban",
    "Ramadan",
    "Shawwal",
    "Dhuʻl-Qa'dah",
    "Dhuʻl-Hijjah",
)


def _hijri_long(hijri_date: str | None) -> str:
    """Long Hijri label from the ``YYYY-MM-DD`` wire value (presentation-only).

    ``None`` renders as an em dash (matching ``hijri``); an unparseable
    value falls back to the raw wire string.
    """
    if hijri_date is None:
        return "—"
    try:
        year_s, month_s, day_s = hijri_date.split("-")
        month = int(month_s)
        if not 1 <= month <= 12:
            raise ValueError(f"hijri month out of range: {hijri_date}")
        return f"{int(day_s)} {HIJRI_MONTHS[month - 1]} {int(year_s)}"
    except ValueError:
        return hijri_date


def _iqamah_hhmm(
    *, day_date: date, hhmm: str, tz: tzinfo | None, rule: IqamahRule
) -> str:
    """Per-card iqamah ``HH:MM`` from core rule data (mirrors domain delays).

    Kept in ``views`` (``api.dto`` + ``core`` only) because the forbidden
    contract bars ``domain`` imports here; semantics match
    ``resolve_iqamah``: delay adds minutes, fixed pins a clock time.
    """
    adhan_at = datetime.combine(day_date, time.fromisoformat(hhmm), tzinfo=tz)
    if rule.mode == "delay":
        return (adhan_at + timedelta(minutes=rule.delay_minutes)).strftime("%H:%M")
    if rule.fixed_time is None:
        raise ConfigError(
            f"fixed iqamah rule without time for prayer: {rule.prayer.value}"
        )
    return datetime.combine(adhan_at.date(), rule.fixed_time, tzinfo=tz).strftime(
        "%H:%M"
    )


def _clock_str(now: datetime, clock_format: str) -> str:
    """Live-clock label for one theme clock format (presentation-only).

    ``12h`` is rendered manually (``%p`` is locale-dependent) so the
    output is deterministic English: ``05:45 AM``, ``12:20 PM``.
    """
    if clock_format == "24h":
        return now.strftime("%H:%M")
    if clock_format == "12h":
        hour = now.hour % 12 or 12
        suffix = "AM" if now.hour < 12 else "PM"
        return f"{hour:02d}:{now.minute:02d} {suffix}"
    return now.strftime("%H:%M:%S")


def build_display_context(
    *,
    day: PrayerDayDTO,
    event: NextEventDTO,
    settings: Settings,
    dim_minutes: int | None = None,
    dim_source: str = "settings",
) -> dict[str, object]:
    """Map resolved DTOs to the display template context (no time reads).

    ``dim_minutes``/``dim_source`` carry the display's effective dim
    (per-display pin, else group pin, else the global default); the
    caller resolves the precedence, the builder only renders it.
    """
    times = {
        "fajr": day.prayers.fajr,
        "dhuhr": day.prayers.dhuhr,
        "asr": day.prayers.asr,
        "maghrib": day.prayers.maghrib,
        "isha": day.prayers.isha,
    }
    next_key = event.next_prayer or "fajr"
    tzinfo = event.now.tzinfo
    labels = (
        PRAYER_LABELS["jumuah"] if next_key == "jumuah" else PRAYER_LABELS[next_key]
    )
    rules = {rule.prayer.value: rule for rule in settings.iqamah_rules}
    cards: list[dict[str, object]] = []
    for key in PRAYER_ORDER:
        is_jumuah_card = key == "dhuhr" and next_key == "jumuah"
        rule_key = "jumuah" if is_jumuah_card else key
        rule = rules.get(rule_key)
        if rule is None:
            raise ConfigError(f"missing iqamah rule for prayer: {rule_key}")
        en, ar = PRAYER_LABELS["jumuah"] if is_jumuah_card else PRAYER_LABELS[key]
        cards.append(
            {
                "key": key,
                "en": en,
                "ar": ar,
                "time": times[key],
                "is_next": key == next_key or is_jumuah_card,
                "iqamah": _iqamah_hhmm(
                    day_date=day.date, hhmm=times[key], tz=tzinfo, rule=rule
                ),
            }
        )
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
    if day.source is ScheduleSource.CALC:
        banners.append("CALC — computed schedule")
    elif day.source is ScheduleSource.MANUAL:
        banners.append("MANUAL — set by admin")
    adhan_date = event.adhan_at.date() if event.adhan_at else None
    theme = settings.theme
    hijri_long = _hijri_long(day.hijri_date)
    return {
        "masjid_name": settings.masjid_name,
        "zone": settings.zone,
        "gregorian": day.date.isoformat(),
        "hijri": day.hijri_date or "—",
        "hijri_long": hijri_long,
        "hijri_display": (
            hijri_long if theme.hijri_form == "long" else (day.hijri_date or "—")
        ),
        "clock": _clock_str(event.now, theme.clock_format),
        "clock_format": theme.clock_format,
        "countdown_inline": theme.countdown_style == "inline",
        "show_boundaries": theme.boundary_strip == "show",
        "body_class": (
            f"palette-{theme.palette} font-{theme.font} density-{theme.density}"
        ),
        "dim_minutes": (
            dim_minutes if dim_minutes is not None else settings.dim_minutes_default
        ),
        "dim_source": dim_source,
        "next_name_en": labels[0],
        "next_name_ar": labels[1],
        "next_time": event.adhan_at.strftime("%H:%M") if event.adhan_at else "",
        "next_tomorrow": adhan_date is not None and adhan_date > day.date,
        "cards": cards,
        "bounds": bounds,
        "banners": banners,
        "next_boundary": event.next_boundary,
        "boundary_at": event.boundary_at.isoformat() if event.boundary_at else None,
        "now_iso": event.now.isoformat(),
        "state": event.state,
        "next_key": next_key,
        "adhan_iso": event.adhan_at.isoformat() if event.adhan_at else "",
        "pre_note": f"Preparing for {labels[0]}" if event.state == "PRE_ADHAN" else "",
        "iqamah_iso": event.iqamah_at.isoformat() if event.iqamah_at else "",
        "dim_until_iso": event.dim_until.isoformat() if event.dim_until else "",
        "adhan_end_iso": (
            (event.adhan_at + timedelta(seconds=settings.adhan_duration_s)).isoformat()
            if event.adhan_at
            else ""
        ),
        "tz_name": tzinfo.key if isinstance(tzinfo, ZoneInfo) else None,
        "tz_offset_min": (
            int(off.total_seconds() // 60)
            if not isinstance(tzinfo, ZoneInfo)
            and (off := event.now.utcoffset()) is not None
            else None
        ),
    }
