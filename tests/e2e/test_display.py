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


def test_display_trilingual_cards(surface: SimpleNamespace, client: TestClient) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for token in ("Subuh", "Zohor", "Isyak", "الفجر", "المغرب"):
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
    assert 'id="dim-skip-hint"' in html
    assert 'id="cards"' not in html
    assert 'id="ftr"' not in html


def test_state_walkthrough_jumuah_friday(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _advance_to(surface, datetime(2025, 10, 24, 12, 20))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "Jumaat" in html
