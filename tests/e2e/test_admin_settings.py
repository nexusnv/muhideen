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


def test_duplicate_prayer_iqamah_rules_are_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    assert client.put("/api/settings", json=payload).status_code == 200
    duplicate = _settings_payload()
    duplicate["iqamah_rules"][1] = dict(duplicate["iqamah_rules"][0])
    assert client.put("/api/settings", json=duplicate).status_code == 422
    # The rejected write must not corrupt the stored rules (was a 500).
    stored = client.get("/api/settings").json()["iqamah_rules"]
    assert stored == payload["iqamah_rules"]


def test_fixed_rule_without_fixed_time_is_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    payload["iqamah_rules"][0] = {
        "prayer": "fajr",
        "mode": "fixed",
        "delay_minutes": 15,
        "fixed_time": None,
    }
    # Was 200 here, then 503 on /api/next-event and /api/events.
    assert client.put("/api/settings", json=payload).status_code == 422


def test_partial_iqamah_rules_are_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    payload["iqamah_rules"] = payload["iqamah_rules"][:1]
    # Was 200 here, then 503 ("missing iqamah rule") on read endpoints.
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


def test_offset_round_trip(surface: SimpleNamespace, client: TestClient) -> None:
    _login(client)
    payload = _settings_payload(imsak_offset_min=5, dhuha_offset_min=20)
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert (body["imsak_offset_min"], body["dhuha_offset_min"]) == (5, 20)
    assert client.get("/api/settings").json()["imsak_offset_min"] == 5


def test_theme_knobs_round_trip(surface: SimpleNamespace, client: TestClient) -> None:
    _login(client)
    payload = _settings_payload(
        theme={
            "palette": "midnight",
            "font": "system",
            "countdown_style": "inline",
            "clock_format": "12h",
            "hijri_form": "short",
            "boundary_strip": "hide",
            "density": "compact",
        }
    )
    assert client.put("/api/settings", json=payload).status_code == 200
    assert client.get("/api/settings").json()["theme"] == payload["theme"]


def test_theme_knobs_default_when_absent(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload()
    del payload["theme"]
    assert client.put("/api/settings", json=payload).status_code == 200
    assert client.get("/api/settings").json()["theme"]["palette"] == "classic-green"


def test_theme_knob_outside_enum_is_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    stored = _settings_payload()
    stored["theme"] = {**stored["theme"], "palette": "midnight"}
    assert client.put("/api/settings", json=stored).status_code == 200
    payload = _settings_payload()
    payload["theme"] = {**payload["theme"], "palette": "neon"}
    assert client.put("/api/settings", json=payload).status_code == 422
    payload = _settings_payload()
    payload["theme"] = {**payload["theme"], "density": "airy"}
    assert client.put("/api/settings", json=payload).status_code == 422
    assert client.get("/api/settings").json()["theme"]["palette"] == "midnight"


def test_settings_api_round_trips_timezone(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    london = _settings_payload(timezone="Europe/London")
    assert client.put("/api/settings", json=london).status_code == 200
    assert client.get("/api/settings").json()["timezone"] == "Europe/London"
    bad = _settings_payload(timezone="Mars/Olympus")
    assert client.put("/api/settings", json=bad).status_code == 422


def test_asr_juristic_round_trip_and_rejection(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    assert client.put("/api/settings", json=_settings_payload()).status_code == 200
    assert client.get("/api/settings").json()["asr_juristic"] == "shafi"
    assert (
        client.put(
            "/api/settings", json=_settings_payload(asr_juristic="hanafi")
        ).status_code
        == 200
    )
    assert client.get("/api/settings").json()["asr_juristic"] == "hanafi"
    assert (
        client.put(
            "/api/settings", json=_settings_payload(asr_juristic="maliki")
        ).status_code
        == 422
    )


def test_adhan_audio_settings_round_trip(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _login(client)
    payload = _settings_payload(
        adhan_audio_enabled=True,
        adhan_volume=40,
        quiet_hours_start="22:00",
        quiet_hours_end="06:00",
        adhan_muted_prayers=["fajr"],
    )
    assert client.put("/api/settings", json=payload).status_code == 200
    body = client.get("/api/settings").json()
    assert body["adhan_audio_enabled"] is True
    assert body["adhan_volume"] == 40
    assert (body["quiet_hours_start"], body["quiet_hours_end"]) == ("22:00", "06:00")
    assert body["adhan_muted_prayers"] == ["fajr"]
