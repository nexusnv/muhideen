"""E2E display surface: regions, cards, banners, slates (slice 1B-1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e


def _seed_settings(surface: SimpleNamespace, **overrides: Any) -> None:
    from muhideen.core.values import Settings

    base: dict[str, Any] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
        "lat": 3.07,
        "lon": 101.69,
    }
    base.update(overrides)
    surface.settings_repo.save(Settings(**base))  # type: ignore[arg-type]


def _advance_to(surface: SimpleNamespace, target: datetime) -> None:
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone(timedelta(hours=8)))
    delta = (target - surface.clock.now()).total_seconds()
    assert delta >= 0, "walkthrough only moves forward"
    surface.clock.advance(delta)


def test_display_renders_all_regions(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    for region in ("hdr", "hero-clock", "hero-next", "cards", "bounds", "ftr"):
        assert f'id="{region}"' in html
    assert html.count('class="card next"') == 1
    assert "1447" in html  # Hijri date present (calc 2026 day + offset 0)


def test_display_english_arabic_cards(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for token in ("Fajr", "Dhuhr", "Isha", "الفجر", "المغرب"):
        assert token in html


def test_display_hides_disabled_imsak(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, imsak_offset_min=0)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bound-imsak"' not in html
    assert 'id="bound-syuruq"' in html


def test_display_calc_fallback_banner(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "CALC" in html  # seeded coords, no cache: calc source is stale


def test_display_boundary_countdown_region(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, boundary_countdown=True)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bound-next"' in html
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bound-next"' not in html


def test_display_before_setup_is_503_slate(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 503
    assert 'id="slate"' in response.text


def test_display_without_resolvable_schedule_is_404_slate(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=None, lon=None)
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 404
    assert 'id="slate"' in response.text


def test_display_carries_realtime_data_attrs(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for attr in ("data-now=", "data-state=", "data-next=", "data-adhan="):
        assert attr in html


def test_display_carries_tzoffset_for_fixed_offset_clock(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'data-tzoffset="480"' in html
    assert "data-tz=" not in html


def test_state_walkthrough_pre_adhan_hides_footer(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    prayer = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    ).json()
    hour, minute = prayer["prayers"]["dhuhr"].split(":")
    target = datetime(2025, 10, 20, int(hour), int(minute)) - timedelta(minutes=2)
    _advance_to(surface, target)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="note-pre"' in html
    assert 'id="ftr"' not in html
    assert 'id="bound-syuruq"' in html
    assert "Preparing for" in html


def test_state_walkthrough_adhan_overlay(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    prayer = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    ).json()
    hour, minute = prayer["prayers"]["dhuhr"].split(":")
    target = datetime(2025, 10, 20, int(hour), int(minute)) + timedelta(seconds=60)
    _advance_to(surface, target)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="overlay-adhan"' in html
    assert "hidden data-now=" in html
    assert 'id="cards"' not in html


def test_state_walkthrough_iqamah_and_dim(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    event = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    ).json()
    iqamah = datetime.fromisoformat(event["iqamah_at"])
    _advance_to(surface, iqamah - timedelta(minutes=1))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="iqamah-hero"' in html
    assert "data-countdown" in html
    _advance_to(surface, iqamah + timedelta(minutes=2))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="dim"' in html
    assert "hidden data-now=" in html
    assert "data-dim-until=" in html
    assert 'id="dim-skip-hint"' in html
    assert 'id="cards"' not in html
    assert 'id="ftr"' not in html


def test_state_walkthrough_jumuah_friday(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _advance_to(surface, datetime(2025, 10, 24, 12, 20))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "Jumuah" in html


def test_hero_marks_tomorrow_fajr(surface: SimpleNamespace, client: TestClient) -> None:
    from datetime import datetime

    _seed_settings(surface)
    _advance_to(surface, datetime(2025, 10, 20, 21, 0))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="next-tomorrow"' in html


def test_display_per_card_iqamah_elements(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="cards"' in html
    for key in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        match = re.search(rf'id="iqamah-{key}">(\d{{2}}:\d{{2}})<', html)
        assert match, f"missing HH:MM iqamah element for {key}"


def test_display_reskin_regions(surface: SimpleNamespace, client: TestClient) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for token in (
        "countdown-box",
        "iqamah-row",
        "brand-block",
        "glow-emerald",
        'id="live-clock"',
        'id="date-hijri"',
    ):
        assert token in html


def _seed_display_override(
    surface: SimpleNamespace, display_id: str, key: str, value: str
) -> None:
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            (display_id, "Main Hall", "Default"),
        )
        conn.execute(
            "INSERT INTO display_settings (display_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(display_id, key) DO UPDATE SET value = excluded.value",
            (display_id, key, value),
        )


def test_display_applies_per_display_theme_overrides(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "theme.palette", "midnight")
    _seed_display_override(surface, "HALL-01", "theme.countdown_style", "inline")
    _seed_display_override(surface, "HALL-01", "theme.clock_format", "12h")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "palette-midnight" in html
    assert "countdown-inline" in html
    assert 'data-clock-format="12h"' in html
    assert "12:20 PM" in html
    assert "palette-classic-green" not in html


def test_display_unknown_id_falls_back_to_global_theme(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "NOPE"}).text
    assert "palette-classic-green" in html
    assert 'data-clock-format="24h-seconds"' in html
    assert 'data-dim-source="settings"' in html


def test_display_applies_per_display_dim_override(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "dim_minutes_override", "30")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'data-dim-minutes="30"' in html
    assert 'data-dim-source="display"' in html
    event = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    ).json()
    iqamah = datetime.fromisoformat(event["iqamah_at"])
    _advance_to(surface, iqamah + timedelta(minutes=2))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="dim"' in html
    dim_until = datetime.fromisoformat(
        re.search(r'data-dim-until="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    )
    assert (dim_until - iqamah).total_seconds() / 60 == 30


def test_display_hides_boundary_strip_on_override(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "theme.boundary_strip", "hide")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bounds"' not in html
    html = client.get("/display", params={"id": "OTHER"}).text
    assert 'id="bounds"' in html


def test_display_applies_group_dim_pin(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
        conn.execute(
            "UPDATE display_groups SET dim_minutes_override = 30 WHERE name = ?",
            ("Default",),
        )
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'data-dim-minutes="30"' in html
    assert 'data-dim-source="group"' in html


def test_display_display_dim_beats_group_dim(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "dim_minutes_override", "25")
    with surface.db.write() as conn:
        conn.execute(
            "UPDATE display_groups SET dim_minutes_override = 30 WHERE name = ?",
            ("Default",),
        )
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'data-dim-minutes="25"' in html
    assert 'data-dim-source="display"' in html


@pytest.mark.parametrize(
    "key,value",
    [
        ("theme.palette", "neon"),
        ("dim_minutes_override", "99"),
        ("dim_minutes_override", "abc"),
    ],
)
def test_display_corrupt_override_slates_503(
    surface: SimpleNamespace, client: TestClient, key: str, value: str
) -> None:
    _seed_settings(surface)
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
        conn.execute(
            "INSERT INTO display_settings (display_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(display_id, key) DO UPDATE SET value = excluded.value",
            ("HALL-01", key, value),
        )
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 503
    assert 'id="slate"' in response.text
