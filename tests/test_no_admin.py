"""Kept-404 vs reintroduced-gated surface (admin Tasks 2–7).

The pre-file-config session/database surface stays gone (404): admin
pages, session auth, full-replace settings, legacy manual-day and
adhan-audio JSON shapes, and display groups. Every path the file-config
admin surface reintroduces is Bearer-gated instead: without a boot
token it answers 503, with a boot token a missing/wrong Bearer answers
401, and a correct Bearer routes (200/201/422/501 by endpoint).
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "no-admin-matrix-token-0123456789ab"


class FakeClock:
    """Pinned clock; mirrors tests/e2e/conftest.py (no shared import)."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono


def _build_app(tmp_path: Path, admin_token: str | None) -> Any:
    """File-backed app over the example config (no database, no users)."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app

    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    cfg = load_config_file(dest)
    clock = FakeClock(PINNED_START)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=clock,  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
        admin_token=admin_token,
    )
    return create_app(deps)


@pytest.fixture
def file_client(tmp_path: Path) -> Any:
    """No boot token: gated endpoints answer 503, kept-404s stay 404."""
    with TestClient(_build_app(tmp_path, None)) as client:
        yield client


@pytest.fixture
def token_client(tmp_path: Path) -> Any:
    """Boot token set: gated endpoints answer 401 without a Bearer."""
    with TestClient(_build_app(tmp_path, ADMIN_TOKEN)) as client:
        yield client


def _auth(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/login",
        "/admin/setup",
        "/admin/settings",
        "/admin/playlists",
        "/api/settings",
        "/api/auth/session",
    ],
)
def test_removed_surface_returns_404(file_client: TestClient, path: str) -> None:
    assert file_client.get(path).status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/auth/login", {"password": "long-enough-password"}),
        ("post", "/api/auth/setup", {"password": "long-enough-password"}),
        ("post", "/api/auth/logout", {}),
        ("put", "/api/settings", {}),
        ("put", "/api/manual-day", {}),
        ("delete", "/api/manual-day", None),
        ("post", "/api/adhan-audio", {}),
        ("delete", "/api/adhan-audio", None),
        ("patch", "/api/display-groups/Default", {}),
    ],
)
def test_removed_mutations_return_404(
    file_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    query = {"date": "2025-10-20"} if path == "/api/manual-day" else None
    response = file_client.request(method, path, json=payload, params=query)
    assert response.status_code == 404


# --- Reintroduced paths: gated (401 without Bearer on a token booted app). ---


def _gated_request(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> Any:
    """One gated request with minimal shape (auth precedes validation)."""
    if path == "/api/media" and method == "post":
        return client.post(
            path,
            data={"kind": "adhan"},
            headers=headers,
        )
    if path == "/api/backup/restore":
        return client.post(
            path,
            files={"file": ("backup.zip", b"junk", "application/zip")},
            headers=headers,
        )
    if path == "/api/config/validate":
        return client.request(method, path, json={}, headers=headers)
    if method == "get":
        return client.get(path, headers=headers)
    if method == "delete":
        return client.delete(path, headers=headers)
    return client.request(method, path, json={}, headers=headers)


_GATED_PATHS: tuple[tuple[str, str], ...] = (
    # Task 2: per-section config PATCH + full/section dry-run validates.
    ("patch", "/api/config/masjid"),
    ("patch", "/api/config/schedule"),
    ("patch", "/api/config/timing"),
    ("patch", "/api/config/theme"),
    ("patch", "/api/config/adhan-audio"),
    ("post", "/api/config/validate"),
    ("post", "/api/config/masjid/validate"),
    ("post", "/api/config/schedule/validate"),
    ("post", "/api/config/timing/validate"),
    ("post", "/api/config/theme/validate"),
    ("post", "/api/config/adhan-audio/validate"),
    # Task 3: playlists + items (preview stays public, see below).
    ("post", "/api/playlists"),
    ("patch", "/api/playlists/x"),
    ("delete", "/api/playlists/x"),
    ("post", "/api/playlists/x/items"),
    ("delete", "/api/playlists/x/items/0"),
    # Task 4: display writes (the collection read stays public).
    ("put", "/api/displays/x"),
    ("patch", "/api/displays/x"),
    ("delete", "/api/displays/x"),
    # Task 5: manual-days writes + dry-run (the effective-pins read stays public).
    ("put", "/api/config/manual-days/2026-05-01"),
    ("delete", "/api/config/manual-days/2026-05-01"),
    ("post", "/api/config/manual-days/validate"),
    # Task 6: media upload/delete (the file list stays public).
    ("post", "/api/media"),
    ("delete", "/api/media/x.mp3"),
    # Task 7: backup export (GET) + restore (multipart) + logs (GET).
    ("get", "/api/backup/export"),
    ("post", "/api/backup/restore"),
    ("get", "/api/logs"),
)


@pytest.mark.parametrize(("method", "path"), _GATED_PATHS)
def test_reintroduced_paths_require_bearer(
    token_client: TestClient, method: str, path: str
) -> None:
    """Every reintroduced gated path answers 401 with no/wrong Bearer."""
    assert _gated_request(token_client, method, path, {}).status_code == 401
    bad = {"Authorization": "Bearer wrong-token"}
    assert _gated_request(token_client, method, path, bad).status_code == 401


@pytest.mark.parametrize(("method", "path"), _GATED_PATHS)
def test_reintroduced_paths_disabled_without_boot_token(
    file_client: TestClient, method: str, path: str
) -> None:
    """Without a boot token the same paths answer 503 (never 401/404)."""
    assert _gated_request(file_client, method, path, _auth()).status_code == 503


# --- Reintroduced paths: served (public reads stay 200, gated route with Bearer). ---


def test_reintroduced_public_reads_serve(token_client: TestClient) -> None:
    """Reads stay public (spec §1): no Bearer needed, even token-booted."""
    assert token_client.get("/api/playlists").status_code == 200
    preview = token_client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert preview.status_code == 200
    assert token_client.get("/api/displays").status_code == 200
    pins = token_client.get("/api/config/manual-days")
    assert pins.status_code == 200
    assert pins.json()["source"] == "inline"
    assert token_client.get("/api/media").status_code == 200


def test_reintroduced_backup_export_serves_zip_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 7 export routes with a Bearer (zip, relative members)."""
    response = token_client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "muhideen.json" in zf.namelist()
        assert "manifest.json" in zf.namelist()


def test_reintroduced_backup_restore_round_trips_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 7 restore routes with a Bearer (export then restore → ok)."""
    exported = token_client.get("/api/backup/export", headers=_auth())
    assert exported.status_code == 200
    response = token_client.post(
        "/api/backup/restore",
        files={"file": ("backup.zip", exported.content, "application/zip")},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_reintroduced_logs_route_with_bearer(token_client: TestClient) -> None:
    """Task 7 logs routes with a Bearer (200 live, 501 journald-less)."""
    response = token_client.get("/api/logs", params={"lines": 5}, headers=_auth())
    assert response.status_code in (200, 501)
    bad_lines = token_client.get("/api/logs", params={"lines": 0}, headers=_auth())
    assert bad_lines.status_code == 422


def test_reintroduced_config_write_routes_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 2 PATCH + validate route with a Bearer (spot check)."""
    patched = token_client.patch(
        "/api/config/masjid", json={"name": "Matrix Masjid"}, headers=_auth()
    )
    assert patched.status_code == 200
    assert token_client.get("/api/config/masjid").json()["name"] == "Matrix Masjid"
    validated = token_client.post(
        "/api/config/masjid/validate", json={"name": "Matrix Masjid"}, headers=_auth()
    )
    assert validated.status_code == 200


def test_reintroduced_playlist_write_routes_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 3 playlist create routes with a Bearer (201, then 409)."""
    created = token_client.post(
        "/api/playlists", json={"id": "matrix", "title": "Matrix"}, headers=_auth()
    )
    assert created.status_code == 201
    again = token_client.post(
        "/api/playlists", json={"id": "matrix", "title": "Matrix"}, headers=_auth()
    )
    assert again.status_code == 409


def test_reintroduced_display_write_routes_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 4 display upsert routes with a Bearer (201 create)."""
    response = token_client.put("/api/displays/MATRIX-01", json={}, headers=_auth())
    assert response.status_code == 201


def test_reintroduced_manual_days_write_routes_with_bearer(
    token_client: TestClient,
) -> None:
    """Task 5 pin upsert routes with a Bearer (full-day pin, 200)."""
    pin = {
        "date": "2026-05-01",
        "imsak": "05:38",
        "fajr": "05:48",
        "syuruq": "06:55",
        "dhuha": "07:23",
        "dhuhr": "13:05",
        "asr": "16:20",
        "maghrib": "19:05",
        "isha": "20:20",
    }
    response = token_client.put(
        "/api/config/manual-days/2026-05-01", json=pin, headers=_auth()
    )
    assert response.status_code == 200


def test_reintroduced_media_write_gated_not_found(token_client: TestClient) -> None:
    """Task 6 media DELETE routes with a Bearer (unknown path → 404)."""
    response = token_client.delete("/api/media/nope.mp3", headers=_auth())
    assert response.status_code == 404


def test_kept_public_surface_still_serves(file_client: TestClient) -> None:
    assert file_client.get("/api/version").status_code == 200
    day = file_client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert day.status_code == 200
    event = file_client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert event.status_code == 200
    display = file_client.get("/display", params={"id": "main-hall"})
    assert display.status_code == 200
