"""Builder unit tests: tz passthrough, manual banner (1B-1 review)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

pytestmark = pytest.mark.unit
KL = ZoneInfo("Asia/Kuala_Lumpur")


def _dtos(tz, source="jakim"):
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
        stale=False,
        next_boundary=None,
        boundary_at=None,
        time_synced=True,
    )
    return day, event


def _settings():
    from muhideen.core.values import Settings

    return Settings(masjid_name="M", zone="SGR01", hijri_offset=0)


def test_tz_name_iana_passthrough() -> None:
    from muhideen.views.display import build_display_context

    day, event = _dtos(KL)
    ctx = build_display_context(day=day, event=event, settings=_settings())
    assert ctx["tz_name"] == "Asia/Kuala_Lumpur"
    assert ctx["tz_offset_min"] is None


def test_tz_offset_minutes_for_fixed_offset() -> None:
    from muhideen.views.display import build_display_context

    day, event = _dtos(timezone(timedelta(hours=8)))
    ctx = build_display_context(day=day, event=event, settings=_settings())
    assert ctx["tz_offset_min"] == 480
    assert ctx["tz_name"] is None


def test_tz_name_absent_for_fixed_offset() -> None:
    from muhideen.views.display import build_display_context

    day, event = _dtos(timezone(timedelta(hours=8)))
    ctx = build_display_context(day=day, event=event, settings=_settings())
    assert ctx["tz_name"] is None


def test_manual_source_banner() -> None:
    from muhideen.views.display import build_display_context

    day, event = _dtos(KL, source="manual")
    ctx = build_display_context(day=day, event=event, settings=_settings())
    assert "MANUAL — set by admin" in ctx["banners"]
    assert not any("CALC" in b for b in ctx["banners"])
