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
    assert (ctx["next_name_en"], ctx["next_name_bm"]) == ("Jumuah", "Jumaat")
    assert ctx["next_time"] == "12:15"
