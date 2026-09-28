"""Display state contexts: per-state builder outputs (slice 1B-2)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from muhideen.api.dto import NextEventDTO, PrayerDayDTO

pytestmark = pytest.mark.unit

KL = ZoneInfo("Asia/Kuala_Lumpur")


def _day() -> PrayerDayDTO:
    return PrayerDayDTO(
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
        source="jakim",
        stale=False,
        hijri_date="1447-04-28",
    )


def _event(state: str, **overrides: object) -> NextEventDTO:
    base: dict[str, object] = {
        "state": state,
        "now": datetime(2025, 10, 20, 12, 20, tzinfo=KL),
        "next_prayer": "dhuhr",
        "adhan_at": datetime(2025, 10, 20, 12, 15, tzinfo=KL),
        "iqamah_at": datetime(2025, 10, 20, 12, 25, tzinfo=KL),
        "dim_until": datetime(2025, 10, 20, 12, 45, tzinfo=KL),
        "stale": False,
        "next_boundary": None,
        "boundary_at": None,
        "time_synced": True,
    }
    base.update(overrides)
    return NextEventDTO(**base)  # type: ignore[arg-type]


def _settings() -> object:
    from muhideen.core.values import Settings

    return Settings(masjid_name="M", zone="SGR01", hijri_offset=0)


def test_pre_adhan_note_names_next_prayer() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("PRE_ADHAN"), settings=_settings()
    )  # type: ignore[arg-type]
    assert ctx["state"] == "PRE_ADHAN"
    assert "Dhuhr" in str(ctx["pre_note"])


def test_normal_has_empty_pre_note() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )  # type: ignore[arg-type]
    assert ctx["pre_note"] == ""


def test_adhan_end_derives_from_duration() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(day=_day(), event=_event("ADHAN"), settings=_settings())  # type: ignore[arg-type]
    assert ctx["adhan_end_iso"] == "2025-10-20T12:18:00+08:00"


def test_iqamah_and_dim_targets_pass_through() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("IQAMAH_COUNTDOWN"), settings=_settings()
    )  # type: ignore[arg-type]
    assert ctx["iqamah_iso"] == "2025-10-20T12:25:00+08:00"
    ctx = build_display_context(
        day=_day(), event=_event("SALAH_DIM"), settings=_settings()
    )  # type: ignore[arg-type]
    assert ctx["dim_until_iso"] == "2025-10-20T12:45:00+08:00"


def test_jumuah_labels_and_dhuhr_time() -> None:
    from muhideen.views.display import build_display_context

    event = _event("NORMAL", next_prayer="jumuah")
    ctx = build_display_context(day=_day(), event=event, settings=_settings())  # type: ignore[arg-type]
    assert (ctx["next_name_en"], ctx["next_name_ar"]) == ("Jumuah", "الجمعة")
    assert ctx["next_time"] == "12:15"


def test_next_tomorrow_flag_when_adhan_is_next_day() -> None:
    from datetime import datetime

    from muhideen.views.display import build_display_context

    day = _day()
    event = _event(
        "NORMAL",
        now=datetime(2025, 10, 20, 21, 0, tzinfo=KL),
        next_prayer="fajr",
        adhan_at=datetime(2025, 10, 21, 5, 45, tzinfo=KL),
    )
    ctx = build_display_context(day=day, event=event, settings=_settings())  # type: ignore[arg-type]
    assert ctx["next_tomorrow"] is True


def test_next_tomorrow_false_same_day() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )  # type: ignore[arg-type]
    assert ctx["next_tomorrow"] is False


def _cards_by_key(ctx: object) -> dict[str, dict[str, object]]:
    from typing import cast

    assert isinstance(ctx, dict)
    cards = cast(list[dict[str, object]], ctx["cards"])
    return {str(c["key"]): c for c in cards}


def test_cards_carry_iqamah_times() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )  # type: ignore[arg-type]
    by_key = _cards_by_key(ctx)
    assert by_key["fajr"]["iqamah"] == "06:00"
    assert by_key["dhuhr"]["iqamah"] == "12:25"
    assert by_key["asr"]["iqamah"] == "15:40"
    assert by_key["maghrib"]["iqamah"] == "18:15"
    assert by_key["isha"]["iqamah"] == "19:40"


def test_hijri_long_format() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )  # type: ignore[arg-type]
    assert isinstance(ctx, dict)
    assert ctx["hijri_long"] == "28 Rabi' al-Thani 1447"


def test_hijri_long_falls_back_to_raw_wire_value() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day().model_copy(update={"hijri_date": None}),
        event=_event("NORMAL"),
        settings=_settings(),
    )  # type: ignore[arg-type]
    assert isinstance(ctx, dict)
    assert ctx["hijri_long"] == "—"
    ctx = build_display_context(
        day=_day().model_copy(update={"hijri_date": "1447-13-01"}),
        event=_event("NORMAL"),
        settings=_settings(),
    )  # type: ignore[arg-type]
    assert isinstance(ctx, dict)
    assert ctx["hijri_long"] == "1447-13-01"


def _custom_settings() -> object:
    from muhideen.core.values import (
        DEFAULT_IQAMAH_RULES,
        IqamahRule,
        MarkerName,
        Settings,
    )

    rules = tuple(
        IqamahRule(prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=45)
        if r.prayer is MarkerName.JUMUAH
        else r
        for r in DEFAULT_IQAMAH_RULES
    )
    return Settings(masjid_name="M", zone="SGR01", hijri_offset=0, iqamah_rules=rules)


def test_dhuhr_card_uses_jumuah_rule_and_labels() -> None:
    from datetime import date, datetime

    from muhideen.views.display import build_display_context

    friday = _day().model_copy(update={"date": date(2025, 10, 24)})
    event = _event(
        "NORMAL",
        next_prayer="jumuah",
        now=datetime(2025, 10, 24, 12, 20, tzinfo=KL),
        adhan_at=datetime(2025, 10, 24, 12, 15, tzinfo=KL),
    )
    ctx = build_display_context(day=friday, event=event, settings=_custom_settings())  # type: ignore[arg-type]
    by_key = _cards_by_key(ctx)
    assert by_key["dhuhr"]["iqamah"] == "13:00"
    assert (by_key["dhuhr"]["en"], by_key["dhuhr"]["ar"]) == ("Jumuah", "الجمعة")
    assert by_key["dhuhr"]["is_next"] is True
    assert by_key["fajr"]["iqamah"] == "06:00"


def test_fixed_iqamah_rule_renders_clock_time() -> None:
    from datetime import time

    from muhideen.core.values import (
        DEFAULT_IQAMAH_RULES,
        IqamahRule,
        MarkerName,
        Settings,
    )
    from muhideen.views.display import build_display_context

    rules = tuple(
        IqamahRule(
            prayer=MarkerName.ASR,
            mode="fixed",
            delay_minutes=10,
            fixed_time=time(16, 0),
        )
        if r.prayer is MarkerName.ASR
        else r
        for r in DEFAULT_IQAMAH_RULES
    )
    settings = Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, iqamah_rules=rules
    )
    ctx = build_display_context(day=_day(), event=_event("NORMAL"), settings=settings)
    assert _cards_by_key(ctx)["asr"]["iqamah"] == "16:00"


def test_builder_iqamah_matches_domain_across_modes() -> None:
    from datetime import time

    from muhideen.core.errors import ConfigError
    from muhideen.core.values import (
        DEFAULT_IQAMAH_RULES,
        IqamahRule,
        MarkerName,
        Settings,
    )
    from muhideen.domain.iqamah import resolve_iqamah
    from muhideen.views.display import _iqamah_hhmm, build_display_context

    rules = {r.prayer: r for r in DEFAULT_IQAMAH_RULES}
    day_date = date(2025, 10, 20)

    def _adhan(hhmm: str) -> datetime:
        hour, minute = map(int, hhmm.split(":"))
        return datetime(
            day_date.year, day_date.month, day_date.day, hour, minute, tzinfo=KL
        )

    # delay mode on two prayers with different delays: fajr-15 vs dhuhr-10
    for prayer, hhmm in ((MarkerName.FAJR, "05:45"), (MarkerName.DHUHR, "12:15")):
        rule = rules[prayer]
        adhan_at = _adhan(hhmm)
        assert _iqamah_hhmm(
            day_date=day_date, hhmm=hhmm, tz=KL, rule=rule
        ) == resolve_iqamah(prayer, adhan_at, rules).strftime("%H:%M")

    # fixed clock pin: asr pinned 16:00 on both sides
    fixed_rule = IqamahRule(
        prayer=MarkerName.ASR, mode="fixed", delay_minutes=10, fixed_time=time(16, 0)
    )
    fixed_rules = dict(rules)
    fixed_rules[MarkerName.ASR] = fixed_rule
    assert (
        _iqamah_hhmm(day_date=day_date, hhmm="15:30", tz=KL, rule=fixed_rule) == "16:00"
    )
    assert (
        resolve_iqamah(MarkerName.ASR, _adhan("15:30"), fixed_rules).strftime("%H:%M")
        == "16:00"
    )

    # missing rule -> ConfigError on both sides (Settings rejects partial
    # sets at construction, so empty is the only reachable missing state
    # for the builder; the domain side deletes one rule from the dict).
    with pytest.raises(ConfigError):
        resolve_iqamah(
            MarkerName.DHUHR,
            _adhan("12:15"),
            {k: v for k, v in rules.items() if k is not MarkerName.DHUHR},
        )
    empty_settings = Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, iqamah_rules=()
    )
    with pytest.raises(ConfigError):
        build_display_context(
            day=_day(), event=_event("NORMAL"), settings=empty_settings
        )
