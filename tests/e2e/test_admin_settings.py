"""E2E admin settings: round-trips, validation, live reload (slice 1A-7)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


def _settings_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / "settings.json").read_text())
    payload.update(overrides)
    return payload


def _login(client: TestClient) -> TestClient:
    client.post("/api/auth/setup", json={"password": "password123"})
    return client


def test_get_round_trip_incl_fixed_time_rule(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    payload["iqamah_rules"][0] = {
        "prayer": "fajr",
        "mode": "fixed",
        "delay_minutes": 15,
        "fixed_time": "05:55",
    }
    assert client.put("/api/settings", json=payload).status_code == 200
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["iqamah_rules"][0]["fixed_time"] == "05:55"


def test_boundary_countdown_flip_persisted(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload(boundary_countdown=True)
    assert client.put("/api/settings", json=payload).status_code == 200
    assert client.get("/api/settings").json()["boundary_countdown"] is True


def test_put_publishes_config_update(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    assert client.put("/api/settings", json=payload).status_code == 200
    subscriber = surface.bus.subscribe()
    try:
        assert client.put("/api/settings", json=payload).status_code == 200
        assert subscriber.get(timeout=1.0) == ("config-update", ("settings",))
    finally:
        surface.bus.unsubscribe(subscriber)


def test_hijri_offset_out_of_range_is_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    assert (
        client.put("/api/settings", json=_settings_payload(hijri_offset=3)).status_code
        == 422
    )
    assert (
        client.put("/api/settings", json=_settings_payload(hijri_offset=-3)).status_code
        == 422
    )


def test_boundary_marker_rule_is_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    payload["iqamah_rules"][0]["prayer"] = "syuruq"
    assert client.put("/api/settings", json=payload).status_code == 422


def test_unpaired_lat_lon_is_422(surface: SimpleNamespace, client: TestClient) -> None:
    _login(client)
    payload = _settings_payload(lat=3.07, lon=None)
    assert client.put("/api/settings", json=payload).status_code == 422


def test_unknown_field_is_422(surface: SimpleNamespace, client: TestClient) -> None:
    _login(client)
    payload = _settings_payload(extra_field=1)
    assert client.put("/api/settings", json=payload).status_code == 422


def test_get_after_setup_pre_put_is_503(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    assert client.get("/api/settings").status_code == 503


def test_calc_only_round_trip(surface: SimpleNamespace, client: TestClient) -> None:
    _login(client)
    payload = _settings_payload(calc_only=True)
    assert client.put("/api/settings", json=payload).status_code == 200
    assert client.get("/api/settings").json()["calc_only"] is True
