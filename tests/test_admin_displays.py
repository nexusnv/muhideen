"""Displays PUT/PATCH/DELETE.

Spec S3 over the Task 1 foundation (token gate, shared lock, 422 shape):
display upsert with full-replace defaults, partial PATCH with per-knob
theme inherit, and the public collection read. Pinned FakeClock +
TestClient over a pristine tmp copy of the example config (mirrors the
``test_no_admin`` fixture shape).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"
FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "display-admin-token-0123456789abcdef"

ALL_DEFAULTS = {
    "name": None,
    "language": "en",
    "theme": {
        "palette": None,
        "font": None,
        "countdown_style": None,
        "clock_format": None,
        "hijri_form": None,
        "boundary_strip": None,
        "density": None,
    },
    "dim_minutes_override": None,
    "carousel_enabled": True,
}


class FakeClock:
    """Pinned clock; mirrors tests/e2e/conftest.py (no shared import)."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono


def _auth(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build_app(tmp_path: Path, admin_token: str | None) -> SimpleNamespace:
    """File-backed app over a pristine example copy plus its config path."""
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
    cfg = load_config_file(dest)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
        admin_token=admin_token,
    )
    return SimpleNamespace(app=create_app(deps), dest=dest)


@pytest.fixture
def admin(tmp_path: Path) -> Any:
    """Gated app: Bearer token required for display writes."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest, app=built.app)


@pytest.fixture
def disabled(tmp_path: Path) -> Any:
    """App booted with no token: writes are 503, public reads stay 200."""
    built = _build_app(tmp_path, None)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest, app=built.app)


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _assert_422(response: Any, *loc: str) -> list[dict[str, Any]]:
    """422 carries [{loc, msg}]; optionally pin one entry's full loc."""
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    for entry in detail:
        assert {"loc", "msg"} <= set(entry)
    if loc:
        assert any(entry["loc"] == ["body", *loc] for entry in detail), detail
    return detail


def _raw_displays(dest: Path) -> dict[str, Any]:
    return json.loads(dest.read_text())["displays"]


def _put(admin: Any, did: str, body: dict[str, Any]) -> Any:
    return admin.client.put(f"/api/displays/{did}", json=body, headers=_auth())


# --- PUT upsert ---


def test_put_create_returns_201_with_full_body(admin: Any) -> None:
    """PUT a new id creates it (201) and the row round-trips on disk."""
    response = _put(admin, "lobby", _fixture("admin-display-201.json"))
    assert response.status_code == 201
    assert response.json() == {
        "name": "Lobby",
        "language": "ms",
        "theme": {**ALL_DEFAULTS["theme"], "palette": "midnight"},
        "dim_minutes_override": 30,
        "carousel_enabled": False,
    }
    stored = _raw_displays(admin.dest)
    assert stored["lobby"]["language"] == "ms"
    assert stored["main-hall"]["language"] == "en"


def test_put_empty_body_creates_all_defaults(admin: Any) -> None:
    """An empty body is a valid explicit reset to all-defaults."""
    response = _put(admin, "fresh", {})
    assert response.status_code == 201
    assert response.json() == ALL_DEFAULTS
    assert _raw_displays(admin.dest)["fresh"]["carousel_enabled"] is True


def test_put_replace_returns_200_and_resets_missing(admin: Any) -> None:
    """PUT is full replace: omitted fields reset, never merge."""
    assert _put(admin, "hall", _fixture("admin-display-201.json")).status_code == 201
    response = _put(admin, "hall", {"language": "ar"})
    assert response.status_code == 200
    assert response.json() == {**ALL_DEFAULTS, "language": "ar"}
    stored = _raw_displays(admin.dest)["hall"]
    assert stored["name"] is None
    assert stored["dim_minutes_override"] is None
    assert stored["theme"] == ALL_DEFAULTS["theme"]


def test_put_empty_body_resets_existing(admin: Any) -> None:
    """PUT {} over a customized row restores every default."""
    assert _put(admin, "hall", _fixture("admin-display-201.json")).status_code == 201
    response = _put(admin, "hall", {})
    assert response.status_code == 200
    assert response.json() == ALL_DEFAULTS


def test_put_id_grammar_422(admin: Any) -> None:
    """New ids enforce 1-64 [A-Za-z0-9_-]; overlong/structural fail."""
    before = admin.dest.read_bytes()
    for bad in ("bad id!", "x" * 65, "under_score.ok?"):
        response = _put(admin, bad, {"language": "en"})
        _assert_422(response, "id")
    assert _put(admin, "ok-1_A", {}).status_code == 201
    assert admin.dest.read_bytes() != before


def test_put_unknown_key_422_including_schedule_fork(admin: Any) -> None:
    """Bodies fail closed: unknown keys and schedule/iqamah forks are 422."""
    assert _put(admin, "x", {"bogus": 1}).status_code == 422
    assert _put(admin, "x", {"schedule": {}}).status_code == 422
    assert _put(admin, "x", {"iqamah_rules": []}).status_code == 422
    assert _put(admin, "x", {"theme": {"palette": "nope"}}).status_code == 422


def test_display_fixture_bodies(admin: Any) -> None:
    """The shipped 201/422 samples behave as labelled."""
    created = _put(admin, "lobby", _fixture("admin-display-201.json"))
    assert created.status_code == 201
    assert created.json()["language"] == "ms"
    bad = _put(admin, "lobby2", _fixture("admin-display-422.json"))
    _assert_422(bad, "name")


# --- PATCH partial merge ---


def test_patch_partial_merge_and_null_clears(admin: Any) -> None:
    """Omitted fields stay; explicit null clears nullable name/dim."""
    assert _put(admin, "p", _fixture("admin-display-201.json")).status_code == 201
    response = admin.client.patch(
        "/api/displays/p", json={"language": "ar"}, headers=_auth()
    )
    assert response.status_code == 200
    assert response.json()["language"] == "ar"
    assert response.json()["name"] == "Lobby"
    assert response.json()["dim_minutes_override"] == 30
    cleared = admin.client.patch(
        "/api/displays/p",
        json={"name": None, "dim_minutes_override": None},
        headers=_auth(),
    )
    assert cleared.status_code == 200
    assert cleared.json()["name"] is None
    assert cleared.json()["dim_minutes_override"] is None
    assert cleared.json()["language"] == "ar"
    assert admin.client.get("/api/displays").json()["p"]["name"] is None


def test_patch_theme_knob_merge_and_null_inherits(admin: Any) -> None:
    """Theme merges per knob; null on one knob inherits only that knob."""
    assert _put(admin, "t", _fixture("admin-display-201.json")).status_code == 201
    assert (
        admin.client.patch(
            "/api/displays/t",
            json={"theme": {"palette": "sand", "density": "compact"}},
            headers=_auth(),
        ).status_code
        == 200
    )
    body = admin.client.get("/api/displays").json()["t"]
    assert body["theme"]["palette"] == "sand"
    assert body["theme"]["density"] == "compact"
    assert body["theme"]["font"] is None
    inherit = admin.client.patch(
        "/api/displays/t", json={"theme": {"palette": None}}, headers=_auth()
    )
    assert inherit.status_code == 200
    assert inherit.json()["theme"]["palette"] is None
    assert inherit.json()["theme"]["density"] == "compact"


def test_patch_unknown_id_404(admin: Any) -> None:
    """Patching a missing display is 404."""
    response = admin.client.patch(
        "/api/displays/nope", json={"language": "ms"}, headers=_auth()
    )
    assert response.status_code == 404


def test_patch_rejects_null_theme_object(admin: Any) -> None:
    """The theme object itself is non-nullable; only its knobs inherit."""
    assert _put(admin, "t", {}).status_code == 201
    assert (
        admin.client.patch(
            "/api/displays/t", json={"theme": None}, headers=_auth()
        ).status_code
        == 422
    )


# --- field validation ---


def test_put_and_patch_blank_name_422(admin: Any) -> None:
    """Blank names are 422 on both verbs; disk keeps the old row."""
    _assert_422(_put(admin, "n", {"name": "  "}), "name")
    assert _put(admin, "n", {"name": "Lobby"}).status_code == 201
    before = admin.dest.read_bytes()
    _assert_422(
        admin.client.patch("/api/displays/n", json={"name": "  "}, headers=_auth()),
        "name",
    )
    assert (
        admin.client.patch(
            "/api/displays/n", json={"language": "ms"}, headers=_auth()
        ).status_code
        == 200
    )
    assert _raw_displays(admin.dest)["n"]["name"] == "Lobby"
    assert admin.dest.read_bytes() != before


def test_put_and_patch_unknown_language_422(admin: Any) -> None:
    """Language is a closed literal; unknown values are 422."""
    _assert_422(_put(admin, "l", {"language": "xx"}), "language")
    assert _put(admin, "l", {"language": "bm"}).status_code == 201
    _assert_422(
        admin.client.patch("/api/displays/l", json={"language": "xx"}, headers=_auth()),
        "language",
    )
    assert (
        admin.client.patch(
            "/api/displays/l", json={"language": None}, headers=_auth()
        ).status_code
        == 422
    )


def test_dim_minutes_override_range_and_null(admin: Any) -> None:
    """Dim override is 5-60 or null; out-of-range is 422 on both verbs."""
    assert _put(admin, "d", {"dim_minutes_override": 5}).status_code == 201
    assert _put(admin, "d2", {"dim_minutes_override": 60}).status_code == 201
    _assert_422(_put(admin, "d3", {"dim_minutes_override": 4}), "dim_minutes_override")
    _assert_422(_put(admin, "d4", {"dim_minutes_override": 61}), "dim_minutes_override")
    _assert_422(
        admin.client.patch(
            "/api/displays/d", json={"dim_minutes_override": 100}, headers=_auth()
        ),
        "dim_minutes_override",
    )
    cleared = admin.client.patch(
        "/api/displays/d", json={"dim_minutes_override": None}, headers=_auth()
    )
    assert cleared.status_code == 200
    assert cleared.json()["dim_minutes_override"] is None


# --- DELETE ---


def test_delete_round_trip_and_unknown_404(admin: Any) -> None:
    """Delete removes the row; repeating it (or unknown ids) is 404."""
    assert _put(admin, "gone", {"language": "ms"}).status_code == 201
    gone = admin.client.delete("/api/displays/gone", headers=_auth())
    assert gone.status_code == 200
    assert "gone" not in admin.client.get("/api/displays").json()
    assert admin.client.delete("/api/displays/gone", headers=_auth()).status_code == 404
    assert (
        admin.client.delete("/api/displays/never", headers=_auth()).status_code == 404
    )


# --- legacy rows ---


def test_legacy_out_of_grammar_id_lists_but_new_writes_enforce(admin: Any) -> None:
    """Legacy ids list as-is and stay patchable; new bad ids still fail."""
    raw = json.loads(admin.dest.read_text())
    raw["displays"]["bad id!"] = {"language": "ms"}
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    assert "bad id!" in admin.client.get("/api/displays").json()
    patched = admin.client.patch(
        "/api/displays/bad id!", json={"language": "ar"}, headers=_auth()
    )
    assert patched.status_code == 200
    assert patched.json()["language"] == "ar"
    _assert_422(_put(admin, "another bad!", {}), "id")


def test_slash_id_is_routing_404(admin: Any) -> None:
    """Slash ids are API-invisible: the single-segment route never matches."""
    raw = json.loads(admin.dest.read_text())
    raw["displays"]["a/b"] = {"language": "ms"}
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    assert "a/b" in admin.client.get("/api/displays").json()
    assert admin.client.get("/api/displays/a/b").status_code == 404


# --- reads + gating ---


def test_get_list_serves_seed_rows_in_file_order(admin: Any) -> None:
    """List serves the shipped rows, then created rows in insertion order."""
    listing = admin.client.get("/api/displays")
    assert listing.status_code == 200
    assert list(listing.json()) == ["main-hall", "entrance"]
    assert listing.json()["main-hall"]["language"] == "en"
    assert listing.json()["entrance"]["theme"]["palette"] == "midnight"
    assert _put(admin, "late", {"language": "ar"}).status_code == 201
    assert list(admin.client.get("/api/displays").json()) == [
        "main-hall",
        "entrance",
        "late",
    ]


def test_get_list_is_public(disabled: Any) -> None:
    """The collection read stays public while writes are disabled."""
    assert disabled.client.get("/api/displays").status_code == 200


def test_display_writes_require_token(admin: Any) -> None:
    """Bearer-less (or wrong) writes are 401 with the challenge."""
    response = admin.client.put("/api/displays/x", json={})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    bad = admin.client.put("/api/displays/x", json={}, headers=_auth("wrong"))
    assert bad.status_code == 401
    assert admin.client.delete("/api/displays/main-hall").status_code == 401
    assert (
        admin.client.patch(
            "/api/displays/main-hall", json={"language": "ms"}
        ).status_code
        == 401
    )


def test_display_writes_disabled_without_boot_token(disabled: Any) -> None:
    """No boot token: writes are 503 even carrying a Bearer."""
    assert (
        disabled.client.put(
            "/api/displays/x", json={}, headers=_auth("any-token")
        ).status_code
        == 503
    )
    assert (
        disabled.client.patch(
            "/api/displays/main-hall",
            json={"language": "ms"},
            headers=_auth("any-token"),
        ).status_code
        == 503
    )
    assert (
        disabled.client.delete(
            "/api/displays/main-hall", headers=_auth("any-token")
        ).status_code
        == 503
    )
