"""E2E admin pages: routes, gating, QR block (slice 1B-3)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


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
