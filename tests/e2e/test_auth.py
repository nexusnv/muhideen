"""E2E auth: setup, login, rate limits, sessions (slice 1A-7)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e

_PASSWORD = "password123"


def _setup(client: TestClient, password: str = _PASSWORD) -> Any:
    return client.post("/api/auth/setup", json={"password": password})


def test_setup_issues_session_cookie_with_flags(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = _setup(client)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    cookie = response.headers.get("set-cookie", "")
    assert "muhideen-session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite" in cookie
    assert "Path=/" in cookie


def test_setup_twice_is_409(surface: SimpleNamespace, client: TestClient) -> None:
    assert _setup(client).status_code == 200
    assert _setup(client).status_code == 409


def test_short_password_is_422(surface: SimpleNamespace, client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "short"}).status_code == 422


def test_login_ok(surface: SimpleNamespace, client: TestClient) -> None:
    assert _setup(client).status_code == 200
    fresh = TestClient(surface.app)
    with fresh:
        response = fresh.post("/api/auth/login", json={"password": _PASSWORD})
    assert response.status_code == 200
    assert "muhideen-session=" in response.headers.get("set-cookie", "")


def test_login_wrong_password_is_401(
    surface: SimpleNamespace, client: TestClient
) -> None:
    assert _setup(client).status_code == 200
    response = client.post("/api/auth/login", json={"password": "wrongpass999"})
    assert response.status_code == 401


def test_sixth_attempt_429_even_with_correct_password(
    surface: SimpleNamespace, client: TestClient
) -> None:
    assert _setup(client).status_code == 200
    fresh = TestClient(surface.app)
    with fresh:
        for _ in range(5):
            fresh.post("/api/auth/login", json={"password": "wrongpass999"})
        response = fresh.post("/api/auth/login", json={"password": _PASSWORD})
        assert response.status_code == 429


def test_rate_window_reset_via_advance(
    surface: SimpleNamespace, client: TestClient
) -> None:
    assert _setup(client).status_code == 200
    # Exhaust the login limiter from this IP, then advance past the window.
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "wrongpass999"})
    assert (
        client.post("/api/auth/login", json={"password": _PASSWORD}).status_code == 429
    )
    surface.clock.advance(61.0)
    assert (
        client.post("/api/auth/login", json={"password": _PASSWORD}).status_code == 200
    )


def test_session_expiry_at_1801s(surface: SimpleNamespace, client: TestClient) -> None:
    assert _setup(client).status_code == 200
    surface.clock.advance(1801.0)
    response = client.get("/api/settings")
    assert response.status_code == 401


def test_logout_idempotent(surface: SimpleNamespace, client: TestClient) -> None:
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert _setup(client).status_code == 200
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.post("/api/auth/logout", json={}).status_code == 200


def test_settings_requires_session(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 401


def test_session_endpoint_reports(surface: SimpleNamespace, client: TestClient) -> None:
    payload = client.get("/api/auth/session").json()
    assert payload == {"authenticated": False, "setup_required": True}
    assert _setup(client).status_code == 200
    payload = client.get("/api/auth/session").json()
    assert payload == {"authenticated": True, "setup_required": False}
