"""E2E manual schedule: pin, replace, release, validation, auth (issue #43)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


def _login(client: TestClient) -> None:
    """Create the admin so the client carries a valid session cookie."""
    assert (
        client.post("/api/auth/setup", json={"password": "password123"}).status_code
        == 200
    )


def _seed_settings(client: TestClient) -> None:
    """Install settings (zone SGR01 with calc coordinates for auto fallback)."""
    payload: dict[str, Any] = json.loads((FIXTURES / "settings.json").read_text())
    assert client.put("/api/settings", json=payload).status_code == 200


def _manual_body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / "manual-day.json").read_text())
    payload.update(overrides)
    return payload


def test_put_manual_day_pins_and_echoes_manual_source(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    response = client.put("/api/manual-day", json=_manual_body())
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2025-10-20"
    assert body["zone"] == "SGR01"
    assert body["source"] == "manual"
    assert body["stale"] is True
    assert body["prayers"] == {
        "fajr": "05:45",
        "dhuhr": "12:15",
        "asr": "15:30",
        "maghrib": "18:05",
        "isha": "19:25",
    }
    assert body["boundaries"] == {
        "imsak": "05:35",
        "syuruq": "06:55",
        "dhuha": "07:25",
    }
    assert body["hijri_date"] == "1447-04-28"
    resolved = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["source"] == "manual"
    assert resolved.json()["prayers"]["asr"] == "15:30"


def test_put_manual_day_replaces_existing_pin(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    assert client.put("/api/manual-day", json=_manual_body()).status_code == 200
    response = client.put("/api/manual-day", json=_manual_body(asr="15:45"))
    assert response.status_code == 200
    assert response.json()["source"] == "manual"
    assert response.json()["prayers"]["asr"] == "15:45"
    resolved = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert resolved.json()["prayers"]["asr"] == "15:45"


def test_delete_manual_day_releases_pin_and_falls_back_to_auto(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    assert client.put("/api/manual-day", json=_manual_body()).status_code == 200
    assert (
        client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
        ).json()["source"]
        == "manual"
    )
    response = client.delete("/api/manual-day", params={"date": "2025-10-20"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    resolved = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["source"] == "calc"


def test_delete_manual_day_without_pin_is_404(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    response = client.delete("/api/manual-day", params={"date": "2025-10-20"})
    assert response.status_code == 404


def test_delete_manual_day_leaves_jakim_row_untouched(
    surface: SimpleNamespace, client: TestClient
) -> None:
    # The release is a conditional delete (WHERE source='manual'): a
    # stored automatic row is not a pin, so the DELETE 404s and the row
    # stays servable (closes oracle note #64 on the old check-then-delete).
    _login(client)
    _seed_settings(client)
    _seed_jakim_day(surface, date(2025, 10, 20))
    response = client.delete("/api/manual-day", params={"date": "2025-10-20"})
    assert response.status_code == 404
    resolved = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["source"] == "jakim"
    assert resolved.json()["prayers"]["asr"] == "15:35"


def test_put_manual_day_with_swapped_markers_is_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    swapped = _manual_body(asr="18:05", maghrib="15:30")
    assert client.put("/api/manual-day", json=swapped).status_code == 422
    malformed = _manual_body(fajr="5:45")
    assert client.put("/api/manual-day", json=malformed).status_code == 422


def test_manual_day_requires_admin(
    surface: SimpleNamespace, client: TestClient
) -> None:
    assert client.put("/api/manual-day", json=_manual_body()).status_code == 401
    assert (
        client.delete("/api/manual-day", params={"date": "2025-10-20"}).status_code
        == 401
    )


def _advance_to(surface: SimpleNamespace, target: datetime) -> None:
    """Walk the pinned clock forward (mirrors the test_display.py pattern)."""
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone(timedelta(hours=8)))
    delta = (target - surface.clock.now()).total_seconds()
    assert delta >= 0, "walkthrough only moves forward"
    surface.clock.advance(delta)


def _seed_jakim_day(
    surface: SimpleNamespace,
    day: date,
    zone: str = "SGR01",
) -> None:
    """Seed a fresh JAKIM row with times distinct from the manual fixture."""
    surface.prayer_repo.save_day(
        PrayerDay(
            date=day,
            zone=zone,
            imsak=time(5, 38),
            fajr=time(5, 48),
            syuruq=time(6, 58),
            dhuha=time(7, 28),
            dhuhr=time(12, 18),
            asr=time(15, 35),
            maghrib=time(18, 8),
            isha=time(19, 28),
            source=ScheduleSource.JAKIM,
            fetched_at=surface.clock.now() - timedelta(hours=1),
        )
    )


def test_manual_pin_outranks_jakim_and_calc(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    _seed_settings(client)
    _seed_jakim_day(surface, date(2025, 10, 20))
    before = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert before.status_code == 200
    assert before.json()["source"] == "jakim"
    assert before.json()["prayers"]["asr"] == "15:35"

    assert client.put("/api/manual-day", json=_manual_body()).status_code == 200
    resolved = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["source"] == "manual"
    assert body["prayers"]["asr"] == "15:30"
    assert body["prayers"]["fajr"] == "05:45"

    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "MANUAL" in html
    assert ">3:30<small" in html  # 12h design-locked render of pinned 15:30


def test_december_gap_bridged_by_manual_pin(
    surface: SimpleNamespace, client: TestClient
) -> None:
    # Pin the clock in December; the cache holds nothing past 31-Dec.
    # (Advance before login: sessions expire on the monotonic clock, so
    # the admin acts in December just like production.)
    _advance_to(surface, datetime(2025, 12, 20, 12, 20))
    _login(client)
    _seed_settings(client)
    gap = client.get("/api/prayer-day", params={"date": "2026-01-05", "zone": "SGR01"})
    assert gap.status_code == 200
    assert gap.json()["source"] != "manual"

    assert (
        client.put("/api/manual-day", json=_manual_body(date="2026-01-05")).status_code
        == 200
    )
    resolved = client.get(
        "/api/prayer-day", params={"date": "2026-01-05", "zone": "SGR01"}
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["source"] == "manual"
    assert body["prayers"]["asr"] == "15:30"

    # Advance into January: the display serves the manual pin, not a
    # 404 slate or last-known fallback.
    _advance_to(surface, datetime(2026, 1, 5, 12, 20))
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 200
    assert "MANUAL" in response.text
    assert ">3:30<small" in response.text  # 12h design-locked render of pinned 15:30
