"""E2E public surface: prayer-day, next-event, heartbeat, version (slice 1A-7)."""

from __future__ import annotations

from importlib.metadata import version as package_version
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
    }
    base.update(overrides)
    surface.settings_repo.save(Settings(**base))  # type: ignore[arg-type]


def test_prayer_day_returns_resolved_day_for_seeded_settings(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2025-10-20"
    assert payload["zone"] == "SGR01"
    assert payload["source"] == "calc"


def test_prayer_day_without_resolvable_schedule_is_404(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 404


def test_prayer_day_unknown_zone_is_404(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """An unconfigured zone must 404 even when calc coordinates exist."""
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "ZZZ99"}
    )
    assert response.status_code == 404
    assert "ZZZ99" in response.json()["detail"]


def test_prayer_day_before_setup_is_503(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 503


def test_next_event_accepts_tz_aware_now(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 200
    assert response.json()["state"] in {
        "NORMAL",
        "PRE_ADHAN",
        "ADHAN",
        "IQAMAH_COUNTDOWN",
        "SALAH_DIM",
    }


def test_next_event_rejects_naive_now_with_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get("/api/next-event", params={"now": "2025-10-20T12:20:00"})
    assert response.status_code == 422
    assert "offset" in response.text.lower()


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"date": "not-a-date", "zone": "SGR01"}, 422),
        ({"date": "2025-10-20", "zone": ""}, 422),
    ],
)
def test_malformed_date_and_zone_inputs_are_422(
    surface: SimpleNamespace,
    client: TestClient,
    params: dict[str, str],
    expected: int,
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get("/api/prayer-day", params=params)
    assert response.status_code == expected


def test_version_reports_package_version(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"version": package_version("muhideen"), "api": "v1"}


def test_heartbeat_returns_ok(surface: SimpleNamespace, client: TestClient) -> None:
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name) VALUES (?, ?)",
            ("HALL-01", "Main Hall"),
        )
    response = client.post("/api/displays/heartbeat", json={"id": "HALL-01"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
