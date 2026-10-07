"""Display context builder: DTOs to Jinja context (frontend-owned).

Imports ``api.dto`` + ``core`` only — never ``domain``/``engine``/adapters.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from muhideen.api.dto import NextEventDTO, PrayerDayDTO
from muhideen.core.errors import ConfigError
from muhideen.core.values import ScheduleSource, Settings, theme_css_class

PRAYER_ORDER = ("fajr", "dhuhr", "asr", "maghrib", "isha")

PRAYER_LABELS = {
    "fajr": ("Fajr", "الفجر", "Subuh"),
    "dhuhr": ("Dhuhr", "الظهر", "Zohor"),
    "asr": ("Asr", "العصر", "Asar"),
    "maghrib": ("Maghrib", "المغرب", "Maghrib"),
    "isha": ("Isha", "العشاء", "Isyak"),
    "jumuah": ("Jumuah", "الجمعة", "Jumaat"),
}

BOUNDARY_LABELS = {
    "imsak": ("Imsak", "الإمساك", "Imsak"),
    "syuruq": ("Syuruq", "الشروق", "Syuruk"),
    "dhuha": ("Dhuha", "الضحى", "Dhuha"),
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


def twelve_h(hhmm: str) -> tuple[str, str]:
    """Split ``HH:MM`` into 12-hour ``(h:MM, AM/PM)`` parts (presentation-only).

    The hour carries no leading zero (``05:48`` → ``("5:48", "AM")``);
    midnight/noon map to 12 (``00:15`` → ``("12:15", "AM")``).
    """
    hour_s, minute_s = hhmm.split(":")
    hour = int(hour_s)
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute_s}", suffix


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
    iqamah: dict[str, str],
    dim_minutes: int | None = None,
    dim_source: str = "settings",
    show_carousel: bool = True,
    adhan_audio_url: str | None = None,
    adhan_volume: int = 70,
    language: str = "en",
) -> dict[str, object]:
    """Map resolved DTOs to the display template context (no time reads).

    ``iqamah`` carries the per-card iqamah ``HH:MM`` labels computed
    upstream through the domain Iqamah module; the builder renders them
    and computes nothing. ``dim_minutes``/``dim_source`` carry the
    display's effective dim (per-display pin, else group pin, else the
    global default); ``show_carousel`` carries the display's group
    carousel flag (group pin, else default-on); the caller resolves the
    precedence, the builder only renders it. ``language`` selects the
    primary prayer name (``en``→0, ``ar``→1, ``ms``/``bm``→2) while
    keeping all three labels for compat.
    Colors come only from the effective theme palette (``body_class``
    selects the stylesheet block): the template emits no per-display
    ``<style>`` override, so every surface, border, tint, and accent
    renders from one vetted palette and cannot blend into
    indistinguishability.
    ``countdown_inline`` is retained for compat (unit-pinned) while the
    screen renders the single design-locked ``HH:MM:SS`` format.
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
    lang_index = {"en": 0, "ar": 1, "ms": 2, "bm": 2}.get(language, 0)
    html_lang = "ms" if language == "bm" else language
    cards: list[dict[str, object]] = []
    for key in PRAYER_ORDER:
        is_jumuah_card = key == "dhuhr" and next_key == "jumuah"
        label = iqamah.get(key)
        if label is None:
            raise ConfigError(f"missing iqamah label for prayer: {key}")
        en, ar, bm = PRAYER_LABELS["jumuah"] if is_jumuah_card else PRAYER_LABELS[key]
        time12, period = twelve_h(times[key])
        iqamah12, iqamah_period = twelve_h(label)
        cards.append(
            {
                "key": key,
                "en": en,
                "ar": ar,
                "bm": bm,
                "name": (en, ar, bm)[lang_index],
                "time": times[key],
                "time12": time12,
                "period": period,
                "is_next": key == next_key or is_jumuah_card,
                "iqamah": label,
                "iqamah12": iqamah12,
                "iqamah_period": iqamah_period,
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
        en, ar, bm = BOUNDARY_LABELS[key]
        bound["en"] = en
        bound["ar"] = ar
        bound["bm"] = bm
        bound["name"] = (en, ar, bm)[lang_index]
        time12, period = twelve_h(str(bound["time"]))
        bound["time12"] = time12
        bound["period"] = period
        bound["is_next"] = (
            event.next_boundary is not None and event.next_boundary == key
        )
    banners: list[str] = []
    if not event.time_synced:
        banners.append("TIME UNSYNCED")
    # Source provenance renders as small legend badges, not the warning
    # bar: the bar is reserved for genuine health warnings (clock sync).
    # Explicit offline installs (calc_only, or provider "none") chose
    # that operation mode, so degraded-source badges would nag about a
    # known fact.
    badges: list[str] = []
    if not settings.calc_only and settings.sync_provider != "none":
        if event.stale:
            badges.append("offline")
        if day.source is ScheduleSource.CALC:
            badges.append("calculated")
    if day.source is ScheduleSource.MANUAL:
        badges.append("manual")
    adhan_date = event.adhan_at.date() if event.adhan_at else None
    theme = settings.theme
    hijri_long = _hijri_long(day.hijri_date)
    # Linux-only ``%-d`` (no Windows target; repo is Debian-only per ADR-0005).
    gregorian_long = day.date.strftime("%A %-d %B %Y")
    # Design-locked 12h clock parts for the new screen; ``clock`` keeps the
    # theme-knob value byte-identical (JS overrides it live anyway).
    clock_hm, clock_period = twelve_h(event.now.strftime("%H:%M"))
    adhan_iso = event.adhan_at.isoformat() if event.adhan_at else ""
    iqamah_iso = event.iqamah_at.isoformat() if event.iqamah_at else ""
    # Countdown wording mirrors the ``pre_note`` state precedent: the ticking
    # block is gated on ``countdown_target`` by the template, so a missing
    # target blanks the label too. The adhan countdown shows only in
    # PRE_ADHAN — the state machine enters that state exactly inside the
    # countdown window (domain owns the rule; views never recompute it) —
    # so NORMAL renders a blank countdown area by design.
    if event.state == "PRE_ADHAN":
        countdown_label, countdown_target = (
            f"{labels[lang_index]} call to prayer in",
            adhan_iso,
        )
    elif event.state in ("IQAMAH_COUNTDOWN", "ADHAN"):
        countdown_label, countdown_target = "Iqomah in", iqamah_iso
    else:
        countdown_label, countdown_target = "", ""
    if not countdown_target:
        countdown_label = ""
    return {
        "masjid_name": settings.masjid_name,
        "zone": settings.zone,
        "language": html_lang,
        "gregorian": day.date.isoformat(),
        "gregorian_long": gregorian_long,
        "hijri": day.hijri_date or "—",
        "hijri_long": hijri_long,
        "hijri_display": (
            hijri_long if theme.hijri_form == "long" else (day.hijri_date or "—")
        ),
        "clock": _clock_str(event.now, theme.clock_format),
        "clock_hm": clock_hm,
        "clock_period": clock_period,
        "clock_format": theme.clock_format,
        # Compat-only: the screen renders the unified HH:MM:SS format either
        # way (pinned by test_countdown_format_unified_across_styles); the
        # flag stays for future surfaces / unit pins, the template ignores it.
        "countdown_inline": theme.countdown_style == "inline",
        "show_boundaries": theme.boundary_strip == "show",
        "body_class": theme_css_class(theme),
        "dim_minutes": (
            dim_minutes if dim_minutes is not None else settings.dim_minutes_default
        ),
        "dim_source": dim_source,
        "show_carousel": show_carousel,
        "adhan_audio_url": adhan_audio_url,
        "adhan_volume": adhan_volume,
        "next_name_en": labels[0],
        "next_name_ar": labels[1],
        "next_name_bm": labels[2],
        "next_name": labels[lang_index],
        "next_time": event.adhan_at.strftime("%H:%M") if event.adhan_at else "",
        "next_tomorrow": adhan_date is not None and adhan_date > day.date,
        "cards": cards,
        "bounds": bounds,
        "banners": banners,
        "badges": badges,
        "next_boundary": event.next_boundary,
        "boundary_at": event.boundary_at.isoformat() if event.boundary_at else None,
        "now_iso": event.now.isoformat(),
        "state": event.state,
        "next_key": next_key,
        "adhan_iso": adhan_iso,
        "pre_note": (
            f"Preparing for {labels[lang_index]}" if event.state == "PRE_ADHAN" else ""
        ),
        "countdown_label": countdown_label,
        "countdown_target": countdown_target,
        "iqamah_iso": iqamah_iso,
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
