"""Builder unit tests: tz passthrough, manual banner (1B-1 review)."""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytestmark = pytest.mark.unit
KL = ZoneInfo("Asia/Kuala_Lumpur")


def _dtos(tz, source="jakim", event_stale=False):
    from muhideen.api.dto import NextEventDTO, PrayerDayDTO

    day = PrayerDayDTO(
        date=date(2025, 10, 20),
        zone="SGR01",
        prayers={
            "fajr": "05:45",
            "dhuhr": "12:15",
            "asr": "15:30",
            "maghrib": "18:05",
            "isha": "19:25",
        },
        boundaries={"imsak": "05:35", "syuruq": "06:55", "dhuha": "07:25"},
        source=source,
        stale=False,
        hijri_date="1447-04-28",
    )
    now = datetime(2025, 10, 20, 12, 20, tzinfo=tz)
    event = NextEventDTO(
        state="NORMAL",
        now=now,
        next_prayer="dhuhr",
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=tz),
        iqamah_at=None,
        dim_until=None,
        stale=event_stale,
        next_boundary=None,
        boundary_at=None,
        time_synced=True,
    )
    return day, event


def _settings():
    from muhideen.core.values import Settings

    return Settings(masjid_name="M", zone="SGR01", hijri_offset=0)


def _ctx(day: Any, event: Any, settings: Any, **kwargs: Any) -> Any:
    """Display context with iqamah labels from the domain seam (sole owner)."""
    from muhideen.domain.iqamah import card_iqamah_labels
    from muhideen.views.display import build_display_context as _build

    rules = {rule.prayer: rule for rule in settings.iqamah_rules}
    labels = card_iqamah_labels(
        day.date,
        {
            "fajr": day.prayers.fajr,
            "dhuhr": day.prayers.dhuhr,
            "asr": day.prayers.asr,
            "maghrib": day.prayers.maghrib,
            "isha": day.prayers.isha,
        },
        event.now.tzinfo,
        rules,
        event.next_prayer,
    )
    return _build(day=day, event=event, settings=settings, iqamah=labels, **kwargs)


def test_tz_name_iana_passthrough() -> None:

    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["tz_name"] == "Asia/Kuala_Lumpur"
    assert ctx["tz_offset_min"] is None


def test_tz_offset_minutes_for_fixed_offset() -> None:

    day, event = _dtos(timezone(timedelta(hours=8)))
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["tz_offset_min"] == 480
    assert ctx["tz_name"] is None


def test_tz_name_absent_for_fixed_offset() -> None:

    day, event = _dtos(timezone(timedelta(hours=8)))
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["tz_name"] is None


def test_manual_source_banner() -> None:

    day, event = _dtos(KL, source="manual")
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["badges"] == ["manual"]
    assert ctx["banners"] == []


def test_aladhan_source_has_no_badges() -> None:

    day, event = _dtos(KL, source="aladhan")
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["badges"] == []
    assert ctx["banners"] == []


def test_calc_source_renders_offline_and_calculated_badges() -> None:

    day, event = _dtos(KL, source="calc", event_stale=True)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["badges"] == ["offline", "calculated"]
    assert ctx["banners"] == []


def test_calc_only_suppresses_source_badges() -> None:
    from dataclasses import replace

    settings = replace(_settings(), calc_only=True)
    day, event = _dtos(KL, source="calc", event_stale=True)
    ctx = _ctx(day=day, event=event, settings=settings)
    assert ctx["badges"] == []
    day, event = _dtos(KL, source="manual", event_stale=True)
    ctx = _ctx(day=day, event=event, settings=settings)
    assert ctx["badges"] == ["manual"]


def test_none_provider_suppresses_source_badges() -> None:
    from dataclasses import replace

    settings = replace(_settings(), sync_provider="none")
    day, event = _dtos(KL, source="calc", event_stale=True)
    ctx = _ctx(day=day, event=event, settings=settings)
    assert ctx["badges"] == []


def _theme_settings(**knobs):
    from muhideen.core.values import ThemeSettings

    return ThemeSettings(**knobs)


def _settings_with_theme(**knobs):
    from muhideen.core.values import Settings

    return Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, theme=_theme_settings(**knobs)
    )


def test_countdown_style_maps_to_template_flag() -> None:

    day, event = _dtos(KL)
    assert _ctx(day=day, event=event, settings=_settings())["countdown_inline"] is False
    ctx = _ctx(
        day=day, event=event, settings=_settings_with_theme(countdown_style="inline")
    )
    assert ctx["countdown_inline"] is True


def test_clock_format_variants() -> None:

    day, event = _dtos(KL)
    assert _ctx(day=day, event=event, settings=_settings())["clock"] == "12:20 PM"
    ctx = _ctx(day=day, event=event, settings=_settings_with_theme(clock_format="24h"))
    assert ctx["clock"] == "12:20"
    assert ctx["clock_format"] == "24h"
    ctx = _ctx(
        day=day, event=event, settings=_settings_with_theme(clock_format="24h-seconds")
    )
    assert ctx["clock"] == "12:20:00"
    ctx = _ctx(day=day, event=event, settings=_settings_with_theme(clock_format="12h"))
    assert ctx["clock"] == "12:20 PM"


def test_clock_format_12h_midnight_edge() -> None:
    from datetime import datetime

    day, event = _dtos(KL)
    early = event.model_copy(update={"now": datetime(2025, 10, 20, 5, 45, tzinfo=KL)})
    ctx = _ctx(day=day, event=early, settings=_settings_with_theme(clock_format="12h"))
    assert ctx["clock"] == "05:45 AM"


def test_hijri_form_selects_strip_date() -> None:

    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["hijri_display"] == ctx["hijri_long"] == "28 Rabi' al-Thani 1447"
    ctx = _ctx(day=day, event=event, settings=_settings_with_theme(hijri_form="short"))
    assert ctx["hijri_display"] == "1447-04-28"


def test_boundary_strip_flag() -> None:

    day, event = _dtos(KL)
    assert _ctx(day=day, event=event, settings=_settings())["show_boundaries"] is True
    ctx = _ctx(
        day=day, event=event, settings=_settings_with_theme(boundary_strip="hide")
    )
    assert ctx["show_boundaries"] is False


def test_body_class_carries_palette_font_density() -> None:

    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["body_class"] == "palette-classic-green font-outfit density-comfortable"
    ctx = _ctx(
        day=day,
        event=event,
        settings=_settings_with_theme(
            palette="midnight", font="system", density="compact"
        ),
    )
    assert ctx["body_class"] == "palette-midnight font-system density-compact"


def test_body_class_matches_seam() -> None:
    from muhideen.core.values import theme_css_class

    day, event = _dtos(KL)
    settings = _settings_with_theme(
        palette="midnight",
        font="system",
        countdown_style="inline",
        clock_format="12h",
        hijri_form="short",
        boundary_strip="hide",
        density="compact",
    )
    ctx = _ctx(day=day, event=event, settings=settings)
    assert ctx["body_class"] == theme_css_class(settings.theme)
    assert ctx["body_class"] == "palette-midnight font-system density-compact"


def test_dim_context_defaults_to_settings() -> None:

    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["dim_minutes"] == 20
    assert ctx["dim_source"] == "settings"


def test_dim_context_accepts_display_override() -> None:

    day, event = _dtos(KL)
    ctx = _ctx(
        day=day, event=event, settings=_settings(), dim_minutes=30, dim_source="display"
    )
    assert ctx["dim_minutes"] == 30
    assert ctx["dim_source"] == "display"


def test_context_carries_bm_names() -> None:
    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["cards"][0]["bm"] == "Subuh"
    assert ctx["next_name_bm"] == "Zohor"

    jumuah_event = event.model_copy(update={"next_prayer": "jumuah"})
    jumuah_ctx = _ctx(day=day, event=jumuah_event, settings=_settings())
    assert jumuah_ctx["next_name_bm"] == "Jumaat"
    dhuhr_card = next(c for c in jumuah_ctx["cards"] if c["key"] == "dhuhr")
    assert dhuhr_card["bm"] == "Jumaat"

    bounds_by_key = {b["key"]: b for b in ctx["bounds"]}
    assert bounds_by_key["imsak"]["bm"] == "Imsak"
    assert bounds_by_key["imsak"]["ar"] == "الإمساك"
    assert bounds_by_key["syuruq"]["bm"] == "Syuruk"
    assert bounds_by_key["syuruq"]["ar"] == "الشروق"
    assert bounds_by_key["dhuha"]["bm"] == "Dhuha"
    assert bounds_by_key["dhuha"]["ar"] == "الضحى"

    for theme_settings in (
        _settings_with_theme(palette="midnight"),
        _settings_with_theme(font="system"),
        _settings_with_theme(density="compact"),
    ):
        themed_ctx = _ctx(day=day, event=event, settings=theme_settings)
        assert themed_ctx["cards"][0]["bm"] == "Subuh"
        assert themed_ctx["next_name_bm"] == "Zohor"


def test_context_carries_adhan_audio() -> None:
    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["adhan_audio_url"] is None
    assert ctx["adhan_volume"] == 70
    ctx = _ctx(
        day=day,
        event=event,
        settings=_settings(),
        adhan_audio_url="/static/uploads/adhan.mp3",
        adhan_volume=40,
    )
    assert ctx["adhan_audio_url"] == "/static/uploads/adhan.mp3"
    assert ctx["adhan_volume"] == 40


def test_twelve_h_vectors() -> None:
    from muhideen.views.display import twelve_h

    assert twelve_h("05:48") == ("5:48", "AM")
    assert twelve_h("12:45") == ("12:45", "PM")
    assert twelve_h("00:15") == ("12:15", "AM")
    assert twelve_h("18:05") == ("6:05", "PM")


def test_cards_carry_12h_keys() -> None:
    from muhideen.views.display import twelve_h

    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    by_key = {c["key"]: c for c in ctx["cards"]}
    assert (by_key["fajr"]["time12"], by_key["fajr"]["period"]) == ("5:45", "AM")
    assert (by_key["dhuhr"]["time12"], by_key["dhuhr"]["period"]) == ("12:15", "PM")
    assert (by_key["asr"]["time12"], by_key["asr"]["period"]) == ("3:30", "PM")
    assert (by_key["maghrib"]["time12"], by_key["maghrib"]["period"]) == ("6:05", "PM")
    assert (by_key["isha"]["time12"], by_key["isha"]["period"]) == ("7:25", "PM")
    for card in ctx["cards"]:
        assert twelve_h(str(card["iqamah"])) == (
            card["iqamah12"],
            card["iqamah_period"],
        )


def test_bounds_carry_12h_keys() -> None:
    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    by_key = {b["key"]: b for b in ctx["bounds"]}
    assert (by_key["imsak"]["time12"], by_key["imsak"]["period"]) == ("5:35", "AM")
    assert (by_key["syuruq"]["time12"], by_key["syuruq"]["period"]) == ("6:55", "AM")
    assert (by_key["dhuha"]["time12"], by_key["dhuha"]["period"]) == ("7:25", "AM")


def test_gregorian_long() -> None:
    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["gregorian_long"] == "Monday 20 October 2025"


def test_clock_fallback_split_keys_and_clock_untouched() -> None:
    day, event = _dtos(KL)
    ctx = _ctx(day=day, event=event, settings=_settings())
    assert ctx["clock"] == "12:20 PM"
    assert ctx["clock_hm"] == "12:20"
    assert ctx["clock_period"] == "PM"
    early = event.model_copy(update={"now": datetime(2025, 10, 20, 5, 45, tzinfo=KL)})
    early_ctx = _ctx(day=day, event=early, settings=_settings())
    assert early_ctx["clock_hm"] == "5:45"
    assert early_ctx["clock_period"] == "AM"


def test_countdown_label_target_matrix() -> None:
    day, event = _dtos(KL)
    settings = _settings()

    ctx = _ctx(day=day, event=event, settings=settings)
    assert ctx["countdown_label"] == ""
    assert ctx["countdown_target"] == ""

    pre = event.model_copy(update={"state": "PRE_ADHAN"})
    pre_ctx = _ctx(day=day, event=pre, settings=settings)
    assert pre_ctx["countdown_label"] == "Dhuhr call to prayer in"
    assert pre_ctx["countdown_target"] == event.adhan_at.isoformat()

    iqamah_at = datetime(2025, 10, 20, 12, 30, tzinfo=KL)
    for state in ("IQAMAH_COUNTDOWN", "ADHAN"):
        with_iqamah = event.model_copy(update={"state": state, "iqamah_at": iqamah_at})
        iq_ctx = _ctx(day=day, event=with_iqamah, settings=settings)
        assert iq_ctx["countdown_label"] == "Iqomah in"
        assert iq_ctx["countdown_target"] == iqamah_at.isoformat()

    missing = event.model_copy(update={"state": "IQAMAH_COUNTDOWN", "iqamah_at": None})
    missing_ctx = _ctx(day=day, event=missing, settings=settings)
    assert missing_ctx["countdown_label"] == ""
    assert missing_ctx["countdown_target"] == ""

    dim = event.model_copy(update={"state": "SALAH_DIM"})
    dim_ctx = _ctx(day=day, event=dim, settings=settings)
    assert dim_ctx["countdown_label"] == ""
    assert dim_ctx["countdown_target"] == ""
