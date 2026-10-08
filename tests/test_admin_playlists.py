"""Playlists + items + preview.

Spec S3 over the Task 1 foundation (token gate, shared lock, 422 shape):
playlist CRUD with the shared id grammar enforced on writes only, item
POST/DELETE with per-playlist ``sort_order`` uniqueness, and the public
``preview`` resolver. Pinned FakeClock + TestClient over a pristine tmp
copy of the example config (mirrors the ``test_no_admin`` fixture shape).
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
ADMIN_TOKEN = "playlist-admin-token-0123456789abcdef"


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
    """Gated app: Bearer token required for playlist/item writes."""
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


def _set_coords(dest: Path) -> None:
    """Pin usable coordinates so calc fallback resolves the pinned day."""
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    dest.write_text(json.dumps(raw, indent=2) + "\n")


def _raw_playlists(dest: Path) -> list[dict[str, Any]]:
    return json.loads(dest.read_text())["playlists"]


def _create(admin: Any, body: dict[str, Any]) -> Any:
    return admin.client.post("/api/playlists", json=body, headers=_auth())


# --- POST / GET / DELETE playlists ---


def test_post_minimal_returns_201_with_defaults(admin: Any) -> None:
    """Create fills file-model defaults; the row round-trips on disk."""
    response = _create(admin, {"id": "taraweeh", "title": "Taraweeh Slides"})
    assert response.status_code == 201
    assert response.json() == {
        "id": "taraweeh",
        "title": "Taraweeh Slides",
        "active": True,
        "window_start": None,
        "window_end": None,
        "anchor_marker": None,
        "anchor_start_offset_min": 0,
        "anchor_stop_offset_min": 0,
        "cycle_mode": "indefinite",
        "max_cycles": None,
        "items": [],
    }
    stored = _raw_playlists(admin.dest)
    assert len(stored) == 2
    assert stored[1]["id"] == "taraweeh"


def test_post_requires_id_and_title(admin: Any) -> None:
    """Missing or blank identity fields are 422 and never touch disk."""
    before = admin.dest.read_bytes()
    assert _create(admin, {}).status_code == 422
    assert _create(admin, {"id": "only-id"}).status_code == 422
    assert _create(admin, {"title": "Only Title"}).status_code == 422
    _assert_422(_create(admin, {"id": "   ", "title": "T"}), "id")
    _assert_422(_create(admin, {"id": "x", "title": "   "}), "title")
    assert admin.dest.read_bytes() == before


def test_post_duplicate_id_409(admin: Any) -> None:
    """A second create with the same id conflicts; disk keeps one row."""
    assert _create(admin, {"id": "dup", "title": "First"}).status_code == 201
    response = _create(admin, {"id": "dup", "title": "Second"})
    assert response.status_code == 409
    assert "dup" in response.json()["detail"]
    assert [row["id"] for row in _raw_playlists(admin.dest)].count("dup") == 1


def test_post_duplicate_of_seed_id_409(admin: Any) -> None:
    """The shipped example id is taken like any other."""
    response = _create(admin, {"id": "announcements", "title": "Clone"})
    assert response.status_code == 409


def test_post_id_grammar_422(admin: Any) -> None:
    """Writes enforce 1-64 [A-Za-z0-9_-]; slashes/spaces/overlong fail."""
    before = admin.dest.read_bytes()
    for bad in ("bad id!", "a/b", "x" * 65, "under_score.ok?"):
        response = _create(admin, {"id": bad, "title": "T"})
        _assert_422(response, "id")
    assert _create(admin, {"id": "ok-1_A", "title": "T"}).status_code == 201
    assert admin.dest.read_bytes() != before


def test_post_unknown_key_422(admin: Any) -> None:
    """Create bodies fail closed on unknown keys."""
    response = _create(admin, {"id": "x", "title": "T", "bogus": 1})
    assert response.status_code == 422


def test_get_list_and_detail_round_trip(admin: Any) -> None:
    """List serves the seed row; detail serves the created row."""
    listing = admin.client.get("/api/playlists")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == ["announcements"]
    assert _create(admin, {"id": "late", "title": "Late Show"}).status_code == 201
    detail = admin.client.get("/api/playlists/late")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Late Show"
    assert [row["id"] for row in admin.client.get("/api/playlists").json()] == [
        "announcements",
        "late",
    ]


def test_get_unknown_id_404(admin: Any) -> None:
    """Unknown ids are 404, never 422 or 500."""
    response = admin.client.get("/api/playlists/nope")
    assert response.status_code == 404


def test_delete_round_trip_and_unknown_404(admin: Any) -> None:
    """Delete removes the row; repeating it (or unknown ids) is 404."""
    assert _create(admin, {"id": "gone", "title": "Gone"}).status_code == 201
    gone = admin.client.delete("/api/playlists/gone", headers=_auth())
    assert gone.status_code == 200
    assert admin.client.get("/api/playlists/gone").status_code == 404
    assert (
        admin.client.delete("/api/playlists/gone", headers=_auth()).status_code == 404
    )
    assert (
        admin.client.delete("/api/playlists/never", headers=_auth()).status_code == 404
    )


def test_playlist_fixture_bodies(admin: Any) -> None:
    """The shipped 201/422 samples behave as labelled."""
    created = _create(admin, _fixture("admin-playlist-201.json"))
    assert created.status_code == 201
    assert created.json()["id"] == "taraweeh"
    bad = _create(admin, _fixture("admin-playlist-422.json"))
    _assert_422(bad, "title")


# --- PATCH playlists ---


def test_patch_partial_merge_and_null_clears(admin: Any) -> None:
    """Omitted fields stay; explicit null clears nullable windows."""
    assert _create(admin, {"id": "p", "title": "P"}).status_code == 201
    response = admin.client.patch(
        "/api/playlists/p",
        json={"title": "Renamed", "window_start": "05:00"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["window_start"] == "05:00"
    assert response.json()["active"] is True
    cleared = admin.client.patch(
        "/api/playlists/p", json={"window_start": None}, headers=_auth()
    )
    assert cleared.status_code == 200
    assert cleared.json()["window_start"] is None
    assert admin.client.get("/api/playlists/p").json()["window_start"] is None


def test_patch_unknown_id_404(admin: Any) -> None:
    """Patching a missing playlist is 404."""
    response = admin.client.patch(
        "/api/playlists/nope", json={"title": "T"}, headers=_auth()
    )
    assert response.status_code == 404


def test_patch_id_rename_forbidden_422(admin: Any) -> None:
    """The id key is forbidden even when it restates the same value."""
    assert _create(admin, {"id": "fixed", "title": "F"}).status_code == 201
    before = admin.dest.read_bytes()
    assert (
        admin.client.patch(
            "/api/playlists/fixed", json={"id": "moved"}, headers=_auth()
        ).status_code
        == 422
    )
    assert (
        admin.client.patch(
            "/api/playlists/fixed", json={"id": "fixed"}, headers=_auth()
        ).status_code
        == 422
    )
    assert admin.dest.read_bytes() == before


def test_patch_items_key_forbidden_422(admin: Any) -> None:
    """Items ride the items endpoints; a playlist PATCH carrying them is 422."""
    assert _create(admin, {"id": "noitems", "title": "N"}).status_code == 201
    response = admin.client.patch(
        "/api/playlists/noitems", json={"items": []}, headers=_auth()
    )
    assert response.status_code == 422


def test_patch_blank_title_422(admin: Any) -> None:
    """Blank renames are 422; null titles are 422 (non-nullable)."""
    assert _create(admin, {"id": "t", "title": "T"}).status_code == 201
    _assert_422(
        admin.client.patch("/api/playlists/t", json={"title": "  "}, headers=_auth()),
        "title",
    )
    assert (
        admin.client.patch(
            "/api/playlists/t", json={"title": None}, headers=_auth()
        ).status_code
        == 422
    )


# --- window / anchor / cycle grammar ---


def test_window_grammar_via_parse_window(admin: Any) -> None:
    """Window bounds are HH:MM or any marker name; garbage is 422."""
    assert (
        _create(
            admin, {"id": "w1", "title": "W", "window_start": "not-a-bound"}
        ).status_code
        == 422
    )
    assert (
        _create(admin, {"id": "w1", "title": "W", "window_end": "25:00"}).status_code
        == 422
    )
    for n, good in enumerate(("fajr", " Fajr ", "imsak", "05:30")):
        ok = _create(admin, {"id": f"g-{n}", "title": "G", "window_start": good})
        assert ok.status_code == 201, good


def test_boundary_anchor_422(admin: Any) -> None:
    """Anchors are prayer-only; boundary markers cannot anchor."""
    assert (
        _create(
            admin, {"id": "anch", "title": "A", "anchor_marker": "imsak"}
        ).status_code
        == 422
    )
    assert _create(admin, {"id": "anch", "title": "A"}).status_code == 201
    response = admin.client.patch(
        "/api/playlists/anch", json={"anchor_marker": "dhuha"}, headers=_auth()
    )
    assert response.status_code == 422
    assert (
        admin.client.patch(
            "/api/playlists/anch", json={"anchor_marker": "fajr"}, headers=_auth()
        ).status_code
        == 200
    )


def test_anchor_offsets_must_pair(admin: Any) -> None:
    """stop < start is a misconfiguration, never a midnight span."""
    assert (
        _create(
            admin,
            {
                "id": "off",
                "title": "O",
                "anchor_marker": "fajr",
                "anchor_start_offset_min": 10,
                "anchor_stop_offset_min": 5,
            },
        ).status_code
        == 422
    )
    assert (
        _create(
            admin,
            {
                "id": "off",
                "title": "O",
                "anchor_marker": "fajr",
                "anchor_start_offset_min": 5,
                "anchor_stop_offset_min": 5,
            },
        ).status_code
        == 201
    )


def test_cycle_pairing_422(admin: Any) -> None:
    """repeat needs max_cycles>=1; indefinite needs null."""
    assert (
        _create(admin, {"id": "c1", "title": "C", "cycle_mode": "repeat"}).status_code
        == 422
    )
    assert (
        _create(
            admin,
            {
                "id": "c1",
                "title": "C",
                "cycle_mode": "repeat",
                "max_cycles": 0,
            },
        ).status_code
        == 422
    )
    assert (
        _create(admin, {"id": "c1", "title": "C", "max_cycles": 3}).status_code == 422
    )
    assert (
        _create(
            admin,
            {"id": "c1", "title": "C", "cycle_mode": "repeat", "max_cycles": 2},
        ).status_code
        == 201
    )
    assert (
        admin.client.patch(
            "/api/playlists/c1", json={"cycle_mode": "indefinite"}, headers=_auth()
        ).status_code
        == 422
    )
    assert (
        admin.client.patch(
            "/api/playlists/c1",
            json={"cycle_mode": "indefinite", "max_cycles": None},
            headers=_auth(),
        ).status_code
        == 200
    )


# --- items ---


def _post_item(admin: Any, pid: str, body: dict[str, Any]) -> Any:
    return admin.client.post(f"/api/playlists/{pid}/items", json=body, headers=_auth())


def test_item_post_auto_assign_and_gaps_persist(admin: Any) -> None:
    """Missing sort_order assigns max+1 (empty -> 0); deletes leave gaps."""
    assert _create(admin, {"id": "car", "title": "Carousel"}).status_code == 201
    first = _post_item(admin, "car", {"image_path": "a.jpg", "duration_s": 5})
    assert first.status_code == 201
    assert first.json()["sort_order"] == 0
    second = _post_item(
        admin, "car", {"image_path": "b.jpg", "duration_s": 5, "sort_order": 4}
    )
    assert second.json()["sort_order"] == 4
    third = _post_item(admin, "car", {"image_path": "c.jpg", "duration_s": 5})
    assert third.json()["sort_order"] == 5
    assert (
        admin.client.delete("/api/playlists/car/items/4", headers=_auth()).status_code
        == 200
    )
    refill = _post_item(admin, "car", {"image_path": "d.jpg", "duration_s": 5})
    assert refill.json()["sort_order"] == 6
    orders = [
        item["sort_order"]
        for item in admin.client.get("/api/playlists/car").json()["items"]
    ]
    assert orders == [0, 5, 6]


def test_item_post_duplicate_sort_order_409(admin: Any) -> None:
    """Conflicting with an existing slot is 409; disk keeps one row."""
    assert _create(admin, {"id": "slots", "title": "S"}).status_code == 201
    assert (
        _post_item(
            admin, "slots", {"image_path": "a.jpg", "duration_s": 5, "sort_order": 2}
        ).status_code
        == 201
    )
    response = _post_item(
        admin, "slots", {"image_path": "b.jpg", "duration_s": 5, "sort_order": 2}
    )
    assert response.status_code == 409
    items = admin.client.get("/api/playlists/slots").json()["items"]
    assert [item["sort_order"] for item in items] == [2]


def test_item_post_malformed_422(admin: Any) -> None:
    """Negative order, zero duration, and escaping paths are 422."""
    assert _create(admin, {"id": "mal", "title": "M"}).status_code == 201
    before = admin.dest.read_bytes()
    assert (
        _post_item(
            admin, "mal", {"image_path": "a.jpg", "duration_s": 5, "sort_order": -1}
        ).status_code
        == 422
    )
    assert (
        _post_item(admin, "mal", {"image_path": "a.jpg", "duration_s": 0}).status_code
        == 422
    )
    assert _post_item(admin, "mal", {"duration_s": 5}).status_code == 422
    for bad_path in ("../evil.jpg", "/abs/x.jpg", "media/../../x.jpg", "a?.jpg"):
        response = _post_item(admin, "mal", {"image_path": bad_path, "duration_s": 5})
        assert response.status_code == 422, bad_path
    assert admin.dest.read_bytes() == before


def test_item_post_dangling_path_allowed(admin: Any) -> None:
    """Existence is not required: files may arrive before or after items."""
    assert _create(admin, {"id": "dangle", "title": "D"}).status_code == 201
    response = _post_item(
        admin,
        "dangle",
        {"image_path": "playlists/not-yet-uploaded.jpg", "duration_s": 7},
    )
    assert response.status_code == 201
    assert response.json()["image_path"] == "playlists/not-yet-uploaded.jpg"


def test_item_post_strips_media_prefix(admin: Any) -> None:
    """A legacy media/ prefix normalizes to the media-relative rel."""
    assert _create(admin, {"id": "px", "title": "P"}).status_code == 201
    response = _post_item(
        admin, "px", {"image_path": "media/playlists/welcome.jpg", "duration_s": 5}
    )
    assert response.status_code == 201
    assert response.json()["image_path"] == "playlists/welcome.jpg"


def test_item_post_unknown_playlist_404(admin: Any) -> None:
    """Items on a missing playlist are 404."""
    assert (
        _post_item(admin, "nope", {"image_path": "a.jpg", "duration_s": 5}).status_code
        == 404
    )


def test_item_delete_unknown_404(admin: Any) -> None:
    """Deleting a missing slot (or playlist) is 404."""
    assert _create(admin, {"id": "del", "title": "D"}).status_code == 201
    assert (
        admin.client.delete("/api/playlists/del/items/9", headers=_auth()).status_code
        == 404
    )
    assert (
        admin.client.delete("/api/playlists/nope/items/0", headers=_auth()).status_code
        == 404
    )


def test_item_delete_ambiguous_sort_order_409(admin: Any) -> None:
    """Legacy duplicate slots cannot be addressed until hand-deduped."""
    raw = json.loads(admin.dest.read_text())
    raw["playlists"].append(
        {
            "id": "legacy-dupes",
            "title": "Legacy",
            "items": [
                {"image_path": "first.jpg", "duration_s": 5, "sort_order": 1},
                {"image_path": "second.jpg", "duration_s": 5, "sort_order": 1},
            ],
        }
    )
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    response = admin.client.delete(
        "/api/playlists/legacy-dupes/items/1", headers=_auth()
    )
    assert response.status_code == 409
    items = admin.client.get("/api/playlists/legacy-dupes").json()["items"]
    assert [item["image_path"] for item in items] == ["first.jpg", "second.jpg"]


# --- legacy rows ---


def test_legacy_duplicate_sort_orders_list_in_file_order(admin: Any) -> None:
    """Hand-edited duplicate slots list as-is, file order preserved."""
    raw = json.loads(admin.dest.read_text())
    raw["playlists"].append(
        {
            "id": "legacy-order",
            "title": "Legacy",
            "items": [
                {"image_path": "b.jpg", "duration_s": 5, "sort_order": 1},
                {"image_path": "a.jpg", "duration_s": 5, "sort_order": 1},
                {"image_path": "z.jpg", "duration_s": 5, "sort_order": 0},
            ],
        }
    )
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    items = admin.client.get("/api/playlists/legacy-order").json()["items"]
    assert [item["image_path"] for item in items] == ["b.jpg", "a.jpg", "z.jpg"]


def test_legacy_out_of_grammar_id_lists_but_writes_enforce(admin: Any) -> None:
    """Legacy ids list as-is; new values still face the grammar."""
    raw = json.loads(admin.dest.read_text())
    raw["playlists"].append({"id": "bad id!", "title": "Hand edited", "items": []})
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    ids = [row["id"] for row in admin.client.get("/api/playlists").json()]
    assert "bad id!" in ids
    assert admin.client.get("/api/playlists/bad id!").status_code == 200
    renamed = admin.client.patch(
        "/api/playlists/bad id!", json={"title": "Still here"}, headers=_auth()
    )
    assert renamed.status_code == 200
    assert (
        _post_item(
            admin, "bad id!", {"image_path": "x.jpg", "duration_s": 5}
        ).status_code
        == 201
    )


def test_slash_id_is_routing_404(admin: Any) -> None:
    """Slash ids are API-invisible: the single-segment route never matches."""
    raw = json.loads(admin.dest.read_text())
    raw["playlists"].append({"id": "a/b", "title": "Slashed", "items": []})
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    ids = [row["id"] for row in admin.client.get("/api/playlists").json()]
    assert "a/b" in ids
    assert admin.client.get("/api/playlists/a/b").status_code == 404


# --- preview ---


def test_preview_registered_before_id_route(admin: Any) -> None:
    """Static preview must precede {id}: preview matches the id grammar."""
    from muhideen.api.admin import public_playlist_router

    paths = [
        route.path
        for route in public_playlist_router.routes
        if isinstance(getattr(route, "path", None), str)
    ]
    assert "/api/playlists/preview" in paths
    assert "/api/playlists/{id}" in paths
    assert paths.index("/api/playlists/preview") < paths.index("/api/playlists/{id}")


def test_preview_not_shadowed_by_id_named_preview(admin: Any) -> None:
    """A playlist literally named preview never captures the static route."""
    assert _create(admin, {"id": "preview", "title": "Sneaky"}).status_code == 201
    response = admin.client.get("/api/playlists/preview")
    assert response.status_code == 422


def test_preview_moment_required_and_tz_aware(admin: Any) -> None:
    """Missing and naive moments are 422, never 500."""
    assert admin.client.get("/api/playlists/preview").status_code == 422
    naive = admin.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00"}
    )
    assert naive.status_code == 422
    garbage = admin.client.get(
        "/api/playlists/preview", params={"moment": "not-a-time"}
    )
    assert garbage.status_code == 422


def test_preview_resolves_stage_and_active(admin: Any) -> None:
    """A resolvable schedule previews the stage plus active ids."""
    _set_coords(admin.dest)
    response = admin.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["stage"], str) and body["stage"]
    assert "announcements" in body["active_playlists"]


def test_preview_unresolvable_schedule_404(admin: Any) -> None:
    """No coords, no buffer: the date+zone cannot resolve, like prayer-day."""
    response = admin.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 404


def test_preview_broken_config_503(admin: Any) -> None:
    """Corrupt config surfaces the scrubbed 503, like prayer-day."""
    admin.dest.write_text("{ not json\n")
    response = admin.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 503


def test_preview_is_public(disabled: Any) -> None:
    """Preview and playlist reads stay public while writes are disabled."""
    _set_coords(disabled.dest)
    assert disabled.client.get("/api/playlists").status_code == 200
    assert disabled.client.get("/api/playlists/announcements").status_code == 200
    preview = disabled.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert preview.status_code == 200


# --- auth gating ---


def test_playlist_writes_require_token(admin: Any) -> None:
    """Bearer-less (or wrong) writes are 401 with the challenge."""
    response = admin.client.post("/api/playlists", json={"id": "x", "title": "X"})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    bad = admin.client.post(
        "/api/playlists", json={"id": "x", "title": "X"}, headers=_auth("wrong")
    )
    assert bad.status_code == 401
    assert admin.client.delete("/api/playlists/announcements").status_code == 401


def test_playlist_writes_disabled_without_boot_token(disabled: Any) -> None:
    """No boot token: writes are 503 even carrying a Bearer."""
    assert (
        disabled.client.post(
            "/api/playlists",
            json={"id": "x", "title": "X"},
            headers=_auth("any-token"),
        ).status_code
        == 503
    )
    assert (
        disabled.client.delete(
            "/api/playlists/announcements", headers=_auth("any-token")
        ).status_code
        == 503
    )
