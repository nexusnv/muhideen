"""E2E admin pages: routes, gating, QR block (slice 1B-3)."""

from __future__ import annotations

import html as html_module
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


def _wizard_body(**overrides: Any) -> dict[str, Any]:
    """Exact body the setup wizard submits (admin.js DEFAULTS + wizard fields)."""
    body: dict[str, Any] = {
        "masjid_name": "Masjid Baru",
        "zone": "SGR01",
        "hijri_offset": 0,
        "adhan_duration_s": 180,
        "dim_minutes_default": 20,
        "dim_minutes_jumuah": 45,
        "iqamah_rules": [
            {
                "prayer": "fajr",
                "mode": "delay",
                "delay_minutes": 15,
                "fixed_time": None,
            },
            {
                "prayer": "dhuhr",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
            {
                "prayer": "asr",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
            {
                "prayer": "maghrib",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
            {
                "prayer": "isha",
                "mode": "delay",
                "delay_minutes": 15,
                "fixed_time": None,
            },
            {
                "prayer": "jumuah",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
        ],
        "lat": 3.07,
        "lon": 101.69,
        "method": "MABIMS",
        "boundary_countdown": False,
        "calc_only": True,
        "imsak_offset_min": 10,
        "dhuha_offset_min": 28,
    }
    body.update(overrides)
    return body


def test_login_page_renders_bilingual(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "Kata Laluan" in response.text
    assert 'data-en="Password"' in response.text


def test_setup_page_open_before_setup(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/admin/setup")
    assert response.status_code == 200
    assert "Persediaan" in response.text


def test_setup_page_redirects_after_setup(
    surface: SimpleNamespace, client: TestClient
) -> None:
    client.post("/api/auth/setup", json={"password": "password123"})
    response = client.get("/admin/setup", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/settings"


def test_settings_page_requires_login(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/admin/settings", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/login"


def test_settings_page_renders_qr_and_fallback(
    surface: SimpleNamespace, client: TestClient
) -> None:
    client.post("/api/auth/setup", json={"password": "password123"})
    # POST /api/auth/setup creates the admin only; seed settings via PUT to
    # mirror the wizard flow (setup -> PUT /api/settings) before rendering.
    payload = json.loads((FIXTURES / "settings.json").read_text())
    assert client.put("/api/settings", json=payload).status_code == 200
    html = client.get("/admin/settings").text
    assert "data:image/png;base64," in html
    assert "testserver" in html
    assert 'id="qr-body" hidden' in html
    rules_attr = html.split("data-rules='", 1)[1].split("'", 1)[0]
    rules = json.loads(html_module.unescape(rules_attr))
    assert len(rules) == 6


def test_wizard_equivalent_body_accepted(
    surface: SimpleNamespace, client: TestClient
) -> None:
    client.post("/api/auth/setup", json={"password": "password123"})
    assert client.put("/api/settings", json=_wizard_body()).status_code == 200
    assert client.get("/api/settings").json()["masjid_name"] == "Masjid Baru"


def test_js_defaults_mirror_settings_dto(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    from muhideen.api.dto import SettingsDTO

    response = client.get("/static/admin.js")
    assert response.status_code == 200
    block = response.text.split("var DEFAULTS = {", 1)[1].split("\n};", 1)[0]
    keys = set(re.findall(r'"([a-z_]+)":', block))
    assert set(SettingsDTO.model_fields) <= keys


def test_invalid_offset_rejected(surface: SimpleNamespace, client: TestClient) -> None:
    client.post("/api/auth/setup", json={"password": "password123"})
    # POST /api/auth/setup creates the admin only; seed settings via a full
    # PUT first (settings do not exist until then), then flip one offset.
    assert client.put("/api/settings", json=_wizard_body()).status_code == 200
    body = client.get("/api/settings").json()
    body["imsak_offset_min"] = 11
    assert client.put("/api/settings", json=body).status_code == 422


def test_login_rate_limit_sixth_attempt_429(
    surface: SimpleNamespace, client: TestClient
) -> None:
    client.post("/api/auth/setup", json={"password": "password123"})
    for _ in range(5):
        assert (
            client.post("/api/auth/login", json={"password": "wrongpass1"}).status_code
            == 401
        )
    assert (
        client.post("/api/auth/login", json={"password": "wrongpass1"}).status_code
        == 429
    )
