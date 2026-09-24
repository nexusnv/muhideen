"""E2E docs gate: LAN + admin session required (slice 1A-7)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e


def _lan_client(surface: SimpleNamespace) -> TestClient:
    return TestClient(surface.app, client=("127.0.0.1", 50000))


def _public_client(surface: SimpleNamespace) -> TestClient:
    return TestClient(surface.app, client=("8.8.8.8", 44321))


def test_docs_off_lan_is_404(surface: SimpleNamespace) -> None:
    with _public_client(surface) as client:
        assert client.get("/docs").status_code == 404


def test_docs_on_lan_without_session_is_401(surface: SimpleNamespace) -> None:
    with _lan_client(surface) as client:
        assert client.get("/docs").status_code == 401


def test_docs_on_lan_with_session_is_200(surface: SimpleNamespace) -> None:
    with _lan_client(surface) as client:
        assert (
            client.post("/api/auth/setup", json={"password": "password123"}).status_code
            == 200
        )
        response = client.get("/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower()


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_paths_off_lan_are_404(surface: SimpleNamespace, path: str) -> None:
    with _public_client(surface) as client:
        assert client.get(path).status_code == 404


def test_public_api_not_gated(surface: SimpleNamespace) -> None:
    with _public_client(surface) as client:
        assert client.get("/api/version").status_code == 200
