"""Admin config/display/playlist/manual-day error branches (spec §2/§3).

Second-layer coverage over ``tests/test_admin_config.py``,
``test_admin_displays.py``, ``test_admin_playlists.py`` and
``test_admin_manual_days.py``: raw-file breakage (missing/invalid JSON,
non-object roots and sections), cross-section validation mapping,
pins-completion loc selection, dry-run validate endpoints, playlist
window/grammar fallbacks, display corrupt rows, and manual-day
file/inline failure modes. Pinned FakeClock + TestClient over a pristine
tmp copy of the example config (mirrors the ``test_admin_config``
fixture shape).
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

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "edge-config-token-0123456789abcdef"


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
    """Gated app: Bearer token required for writes and dry-run validates."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest, app=built.app)


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


def _raw(dest: Path) -> dict[str, Any]:
    return json.loads(dest.read_text())


def _write_raw(dest: Path, raw: dict[str, Any]) -> None:
    dest.write_text(json.dumps(raw, indent=2) + "\n")


def _with_boundary_override(dest: Path) -> None:
    """Plant a file-model gap: ConfigFile passes, Settings conversion fails."""
    raw = _raw(dest)
    raw["timing"]["countdown_before_adhan_overrides"] = {"imsak": 5}
    _write_raw(dest, raw)


# --- config plumbing: unreadable/missing state ---


def test_config_missing_file_503(admin: Any) -> None:
    """A deleted config file reads as 503, never 500."""
    admin.dest.unlink()
    response = admin.client.get("/api/config")
    assert response.status_code == 503


@pytest.mark.parametrize("text", ["{ not json\n", "[1, 2]\n"])
def test_config_unparsable_root_503(admin: Any, text: str) -> None:
    """Invalid JSON and non-object roots are 503 (scrubbed detail)."""
    admin.dest.write_text(text)
    response = admin.client.get("/api/config")
    assert response.status_code == 503


def test_config_path_missing_state_503(admin: Any) -> None:
    """No config path on the app state fails safe to 503."""
    admin.app.state.config_path = None
    assert admin.client.get("/api/config").status_code == 503


def test_write_lock_missing_state_503(admin: Any) -> None:
    """No write lock on the app state denies the write with 503."""
    admin.app.state.write_lock = None
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "X"}, headers=_auth()
    )
    assert response.status_code == 503


def test_publish_without_bus_still_200(admin: Any) -> None:
    """A missing event bus skips fan-out; the write still lands 200."""
    admin.app.state.event_bus = None
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "No Bus"}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert _raw(admin.dest)["masjid"]["name"] == "No Bus"


def test_non_object_section_heals_through_validation(admin: Any) -> None:
    """A non-object section reads as empty and merges cleanly (200)."""
    raw = _raw(admin.dest)
    raw["masjid"] = "oops"
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "Healed"}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert _raw(admin.dest)["masjid"]["name"] == "Healed"


def test_mapped_422_keeps_foreign_section_loc(admin: Any) -> None:
    """Whole-file failures outside the patched section keep their loc."""
    raw = _raw(admin.dest)
    raw["timing"]["adhan_duration_s"] = -5
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "X"}, headers=_auth()
    )
    detail = _assert_422(response, "timing", "adhan_duration_s")
    assert detail


def test_settings_failure_maps_to_patched_section_422(admin: Any) -> None:
    """Settings conversion failures after ConfigFile locate the section."""
    _with_boundary_override(admin.dest)
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "X"}, headers=_auth()
    )
    _assert_422(response, "masjid")


# --- schedule: merge depth, pins loc selection ---


def test_schedule_jakim_absent_deep_merges(admin: Any) -> None:
    """A missing nested jakim merges from empty (no sibling to keep)."""
    raw = _raw(admin.dest)
    del raw["schedule"]["jakim"]
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/schedule", json={"jakim": {"zone": "SGR02"}}, headers=_auth()
    )
    assert response.status_code == 200
    assert _raw(admin.dest)["schedule"]["jakim"] == {"zone": "SGR02"}


def test_schedule_zone_change_runs_pins_check(admin: Any) -> None:
    """A zone flip revalidates the inline pins against the new zone."""
    response = admin.client.patch(
        "/api/config/schedule", json={"zone": "SGR02"}, headers=_auth()
    )
    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert _raw(admin.dest)["schedule"]["zone"] == "SGR02"


def test_schedule_jakim_zone_change_runs_pins_check(admin: Any) -> None:
    """A jakim-zone flip revalidates with the (jakim, zone) loc on failure."""
    response = admin.client.patch(
        "/api/config/schedule", json={"jakim": {"zone": "SGR99"}}, headers=_auth()
    )
    assert response.status_code == 200
    assert _raw(admin.dest)["schedule"]["jakim"] == {"zone": "SGR99"}


def test_schedule_unzoned_local_without_ref_change(admin: Any) -> None:
    """No zone anywhere routes local; an unchanged ref still re-checks pins."""
    raw = _raw(admin.dest)
    raw["schedule"]["sync_provider"] = "none"
    raw["schedule"]["jakim"] = {"zone": None}
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/schedule",
        json={"calc_only": True, "manual_days_file": None},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert _raw(admin.dest)["schedule"]["calc_only"] is True


def test_schedule_pins_ref_missing_file_422(admin: Any) -> None:
    """A ref pointing at a missing pins file is 422 at the ref loc."""
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days"] = []
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/schedule",
        json={"manual_days_file": "missing.json"},
        headers=_auth(),
    )
    _assert_422(response, "manual_days_file")


def test_schedule_partial_inline_pins_zone_change_422(admin: Any) -> None:
    """A partial inline pin with no buffer row fails the zone flip (422)."""
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days"] = [{"date": "2026-05-01", "fajr": "05:58"}]
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/config/schedule", json={"zone": "SGR02"}, headers=_auth()
    )
    _assert_422(response, "zone")


def test_schedule_zone_change_skips_pins_without_repo(admin: Any) -> None:
    """No prayer repo on state skips completion; the write still lands."""
    admin.app.state.prayer_repo = None
    response = admin.client.patch(
        "/api/config/schedule", json={"zone": "SGR02"}, headers=_auth()
    )
    assert response.status_code == 200
    assert _raw(admin.dest)["schedule"]["zone"] == "SGR02"


def test_schedule_empty_jakim_object_200(admin: Any) -> None:
    """An empty nested jakim object merges as a no-op (200, zone kept)."""
    response = admin.client.patch(
        "/api/config/schedule", json={"jakim": {}}, headers=_auth()
    )
    assert response.status_code == 200
    assert _raw(admin.dest)["schedule"]["jakim"] == {"zone": "SGR01"}


# --- timing / theme / adhan-audio: scalars, mutes, dry-runs ---


def test_timing_scalars_round_trip(admin: Any) -> None:
    """Scalar timing knobs persist and never need a restart."""
    response = admin.client.patch(
        "/api/config/timing",
        json={"adhan_duration_s": 120, "dim_minutes_default": 25},
        headers=_auth(),
    )
    assert response.json() == {"ok": True, "restart_required": False}
    section = admin.client.get("/api/config/timing").json()
    assert section["adhan_duration_s"] == 120
    assert section["dim_minutes_default"] == 25


def test_timing_validate_dry_run(admin: Any) -> None:
    """The timing dry-run writes nothing and rejects bad overrides."""
    response = admin.client.post(
        "/api/config/timing/validate", json={}, headers=_auth()
    )
    assert response.json() == {"ok": True}
    before = admin.dest.read_bytes()
    bad = admin.client.post(
        "/api/config/timing/validate",
        json={"countdown_before_adhan_overrides": {"bogus": 3}},
        headers=_auth(),
    )
    _assert_422(bad, "countdown_before_adhan_overrides", "bogus")
    assert admin.dest.read_bytes() == before


def test_theme_validate_dry_run(admin: Any) -> None:
    """The theme dry-run writes nothing and rejects bad enums."""
    assert admin.client.post(
        "/api/config/theme/validate", json={}, headers=_auth()
    ).json() == {"ok": True}
    before = admin.dest.read_bytes()
    bad = admin.client.post(
        "/api/config/theme/validate", json={"palette": "nope"}, headers=_auth()
    )
    assert bad.status_code == 422
    assert admin.dest.read_bytes() == before


def test_adhan_audio_validate_dry_run(admin: Any) -> None:
    """The adhan-audio dry-run writes nothing and rejects bad mutes."""
    assert admin.client.post(
        "/api/config/adhan-audio/validate", json={}, headers=_auth()
    ).json() == {"ok": True}
    before = admin.dest.read_bytes()
    bad = admin.client.post(
        "/api/config/adhan-audio/validate",
        json={"muted_prayers": ["fajr2"]},
        headers=_auth(),
    )
    _assert_422(bad, "muted_prayers", "fajr2")
    assert admin.dest.read_bytes() == before


def test_adhan_audio_muted_prayer_ok(admin: Any) -> None:
    """A prayer-only mute persists and never needs a restart."""
    response = admin.client.patch(
        "/api/config/adhan-audio", json={"muted_prayers": ["fajr"]}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert admin.client.get("/api/config/adhan-audio").json()["muted_prayers"] == [
        "fajr"
    ]


def test_adhan_audio_muted_unknown_422(admin: Any) -> None:
    """Unknown mute names are 422 (distinct from boundary markers)."""
    response = admin.client.patch(
        "/api/config/adhan-audio", json={"muted_prayers": ["fajr2"]}, headers=_auth()
    )
    _assert_422(response, "muted_prayers", "fajr2")


# --- full-file validate ---


def _full_candidate(admin: Any) -> dict[str, Any]:
    response = admin.client.get("/api/config")
    assert response.status_code == 200
    return response.json()


def test_validate_full_settings_failure_422(admin: Any) -> None:
    """A full candidate failing Settings conversion is 422 at the body."""
    candidate = _full_candidate(admin)
    candidate["timing"]["countdown_before_adhan_overrides"] = {"imsak": 5}
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]


def test_validate_full_missing_pins_ref_422(admin: Any) -> None:
    """A full candidate naming a missing pins file is 422 at the ref."""
    candidate = _full_candidate(admin)
    candidate["schedule"]["manual_days"] = []
    candidate["schedule"]["manual_days_file"] = "missing.json"
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    _assert_422(response, "schedule", "manual_days_file")


def test_validate_full_partial_pins_no_buffer_422(admin: Any) -> None:
    """Filed pins completing against an empty buffer fail at the ref loc."""
    (admin.dest.parent / "pins.json").write_text(
        json.dumps([{"date": "2026-05-01", "fajr": "05:58"}], indent=2) + "\n"
    )
    candidate = _full_candidate(admin)
    candidate["schedule"]["manual_days"] = []
    candidate["schedule"]["manual_days_file"] = "pins.json"
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    _assert_422(response, "schedule", "manual_days_file")


def test_validate_full_with_empty_pins_file_200(admin: Any) -> None:
    """A ref over an empty pins file validates clean (200, writes nothing)."""
    (admin.dest.parent / "pins.json").write_text("[]\n")
    candidate = _full_candidate(admin)
    candidate["schedule"]["manual_days"] = []
    candidate["schedule"]["manual_days_file"] = "pins.json"
    before = admin.dest.read_bytes()
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    assert response.json() == {"ok": True}
    assert admin.dest.read_bytes() == before


def test_get_manual_days_exclusive_503(admin: Any) -> None:
    """Inline pins plus a set ref stay exclusive on reads (503, hand-fix)."""
    (admin.dest.parent / "pins.json").write_text("[]\n")
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days_file"] = "pins.json"
    _write_raw(admin.dest, raw)
    response = admin.client.get("/api/config/manual-days")
    assert response.status_code == 503


# --- displays: corrupt shapes, foreign-section mapping ---


def test_display_put_validates_foreign_section(admin: Any) -> None:
    """A broken foreign section fails a display upsert with its own loc."""
    raw = _raw(admin.dest)
    raw["timing"]["adhan_duration_s"] = -5
    _write_raw(admin.dest, raw)
    response = admin.client.put(
        "/api/displays/new-one", json={"name": "New"}, headers=_auth()
    )
    _assert_422(response, "timing", "adhan_duration_s")


def test_display_put_settings_failure_422(admin: Any) -> None:
    """A Settings-level file gap fails a display upsert at displays."""
    _with_boundary_override(admin.dest)
    response = admin.client.put(
        "/api/displays/new-one", json={"name": "New"}, headers=_auth()
    )
    _assert_422(response, "displays")


def test_displays_non_object_503(admin: Any) -> None:
    """A non-object displays section is 503 (never 500)."""
    raw = _raw(admin.dest)
    raw["displays"] = []
    _write_raw(admin.dest, raw)
    response = admin.client.put(
        "/api/displays/new-one", json={"name": "New"}, headers=_auth()
    )
    assert response.status_code == 503


def test_display_patch_corrupt_row_422(admin: Any) -> None:
    """A non-dict display row is 422 with an empty body loc."""
    raw = _raw(admin.dest)
    raw["displays"]["wrecked"] = "oops"
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/displays/wrecked", json={"language": "ms"}, headers=_auth()
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]


def test_display_patch_invalid_row_422(admin: Any) -> None:
    """Merging over a mistyped row fails structural validation (422)."""
    raw = _raw(admin.dest)
    raw["displays"]["wrecked"] = {
        "name": 123,
        "language": "en",
        "theme": {},
        "carousel_enabled": True,
    }
    _write_raw(admin.dest, raw)
    response = admin.client.patch(
        "/api/displays/wrecked", json={"language": "ms"}, headers=_auth()
    )
    _assert_422(response, "name")


# --- playlists: grammar fallbacks, corrupt shapes ---


def test_playlist_create_validates_foreign_section(admin: Any) -> None:
    """A broken foreign section fails playlist create with its own loc."""
    raw = _raw(admin.dest)
    raw["timing"]["adhan_duration_s"] = -5
    _write_raw(admin.dest, raw)
    response = admin.client.post(
        "/api/playlists", json={"id": "x", "title": "X"}, headers=_auth()
    )
    _assert_422(response, "timing", "adhan_duration_s")


def test_playlist_create_settings_failure_422(admin: Any) -> None:
    """A Settings-level file gap fails playlist create at playlists."""
    _with_boundary_override(admin.dest)
    response = admin.client.post(
        "/api/playlists", json={"id": "x", "title": "X"}, headers=_auth()
    )
    _assert_422(response, "playlists")


def test_playlists_non_list_503(admin: Any) -> None:
    """A non-list playlists section is 503 (never 500)."""
    raw = _raw(admin.dest)
    raw["playlists"] = {}
    _write_raw(admin.dest, raw)
    response = admin.client.post(
        "/api/playlists", json={"id": "x", "title": "X"}, headers=_auth()
    )
    assert response.status_code == 503


def test_playlist_create_duplicate_orders_422(admin: Any) -> None:
    """Duplicate sort_order within one create body is 422."""
    response = admin.client.post(
        "/api/playlists",
        json={
            "id": "dupes",
            "title": "Dupes",
            "items": [
                {"image_path": "a.png", "duration_s": 5, "sort_order": 0},
                {"image_path": "b.png", "duration_s": 5, "sort_order": 0},
            ],
        },
        headers=_auth(),
    )
    _assert_422(response, "sort_order")


def test_playlist_create_repeat_pairing_422(admin: Any) -> None:
    """Repeat without max_cycles fails at max_cycles (empty model loc)."""
    response = admin.client.post(
        "/api/playlists",
        json={"id": "rep", "title": "Rep", "cycle_mode": "repeat"},
        headers=_auth(),
    )
    _assert_422(response, "max_cycles")


def test_playlist_create_items_cap_422(admin: Any) -> None:
    """Over-50 item bodies fail at items (empty model loc, no max_cycles)."""
    response = admin.client.post(
        "/api/playlists",
        json={
            "id": "big",
            "title": "Big",
            "items": [
                {"image_path": f"{n}.png", "duration_s": 5, "sort_order": n}
                for n in range(51)
            ],
        },
        headers=_auth(),
    )
    _assert_422(response, "items")


def test_playlist_create_boundary_anchor_422(admin: Any) -> None:
    """A boundary anchor marker is 422 at anchor_marker (ValueError path)."""
    response = admin.client.post(
        "/api/playlists",
        json={"id": "anch", "title": "Anch", "anchor_marker": "imsak"},
        headers=_auth(),
    )
    _assert_422(response, "anchor_marker")


def test_playlist_create_bad_window_bound_422(admin: Any) -> None:
    """A non-HH:MM, non-marker window bound is 422 at its field."""
    response = admin.client.post(
        "/api/playlists",
        json={"id": "win", "title": "Win", "window_start": "blah"},
        headers=_auth(),
    )
    _assert_422(response, "window_start")


def test_playlist_create_parse_fault_422(admin: Any, monkeypatch: Any) -> None:
    """A late window-parse ValueError maps through the grammar fallback."""
    import muhideen.api.admin as admin_module

    real_parse = admin_module.parse_window

    def _flaky(playlist: Any) -> Any:
        raise ValueError("odd window failure")

    monkeypatch.setattr(admin_module, "parse_window", _flaky)
    response = admin.client.post(
        "/api/playlists", json={"id": "flaky", "title": "Flaky"}, headers=_auth()
    )
    _assert_422(response)
    assert real_parse is not None


def test_window_grammar_fallback_loc_unit() -> None:
    """Grammar failures without a field keyword locate the bare body."""
    from muhideen.api.admin import _window_grammar_422  # noqa: PLC2701

    exc = _window_grammar_422("something completely odd")
    assert exc.status_code == 422
    assert exc.detail == [
        {"loc": ["body"], "msg": "something completely odd", "type": "value_error"}
    ]


@pytest.mark.parametrize(
    "image_path",
    ["", "a\x00b.png", "dir/", "media//x.png", "media"],
)
def test_playlist_item_bad_paths_422(admin: Any, image_path: str) -> None:
    """Blank/NUL/directory/degenerate-absolute/dot item paths are 422."""
    response = admin.client.post(
        "/api/playlists",
        json={
            "id": "paths",
            "title": "Paths",
            "items": [{"image_path": image_path, "duration_s": 5}],
        },
        headers=_auth(),
    )
    _assert_422(response, "image_path")


def _row_index(dest: Path, pid: str) -> int:
    """Index of one playlist row in the raw file (example ships row zero)."""
    rows = _raw(dest)["playlists"]
    return next(n for n, row in enumerate(rows) if row["id"] == pid)


def test_playlist_item_corrupt_row_422(admin: Any) -> None:
    """Appending to a row whose items are not a list is 422."""
    created = admin.client.post(
        "/api/playlists", json={"id": "corr", "title": "Corr"}, headers=_auth()
    )
    assert created.status_code == 201
    raw = _raw(admin.dest)
    raw["playlists"][_row_index(admin.dest, "corr")]["items"] = "oops"
    _write_raw(admin.dest, raw)
    response = admin.client.post(
        "/api/playlists/corr/items",
        json={"image_path": "a.png", "duration_s": 5},
        headers=_auth(),
    )
    _assert_422(response, "items")


def test_playlist_item_nondict_slot_auto_assign_422(admin: Any) -> None:
    """Non-dict slots never match an order; the row still fails validation."""
    created = admin.client.post(
        "/api/playlists", json={"id": "slot", "title": "Slot"}, headers=_auth()
    )
    assert created.status_code == 201
    raw = _raw(admin.dest)
    raw["playlists"][_row_index(admin.dest, "slot")]["items"] = ["oops"]
    _write_raw(admin.dest, raw)
    response = admin.client.post(
        "/api/playlists/slot/items",
        json={"image_path": "a.png", "duration_s": 5},
        headers=_auth(),
    )
    assert response.status_code == 422


def test_preview_engine_missing_503(admin: Any) -> None:
    """No engine on state fails the preview safe to 503."""
    admin.app.state.engine = None
    response = admin.client.get(
        "/api/playlists/preview", params={"moment": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 503


# --- manual days: file/inline failure modes ---


def _full_pin() -> dict[str, str]:
    return {
        "imsak": "05:48",
        "fajr": "05:58",
        "syuruq": "07:05",
        "dhuha": "07:33",
        "dhuhr": "13:15",
        "asr": "16:30",
        "maghrib": "19:15",
        "isha": "20:30",
    }


def _set_pins_ref(dest: Path, ref: str) -> None:
    raw = _raw(dest)
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = ref
    _write_raw(dest, raw)


def test_put_manual_day_empty_markers_422(admin: Any) -> None:
    """A markerless pin corrects nothing: 422 at the date loc."""
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json={}, headers=_auth()
    )
    _assert_422(response, "date")


def test_put_manual_day_file_mode_corrupt_422(admin: Any) -> None:
    """An existing-but-corrupt pins file fails the upsert at the ref."""
    _set_pins_ref(admin.dest, "pins.json")
    (admin.dest.parent / "pins.json").write_text("[{not valid\n")
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=_full_pin(), headers=_auth()
    )
    _assert_422(response, "manual_days_file")


def test_put_manual_day_file_mode_mkdir_fails_503(admin: Any) -> None:
    """An unwritable pins parent (path blocked by a file) is 503."""
    blocker = admin.dest.parent / "sub"
    blocker.write_text("in the way")
    _set_pins_ref(admin.dest, "sub/pins.json")
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=_full_pin(), headers=_auth()
    )
    assert response.status_code == 503


def test_put_manual_day_inline_non_list_503(admin: Any) -> None:
    """A non-list inline manual_days is 503 (never 500)."""
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days"] = "oops"
    _write_raw(admin.dest, raw)
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=_full_pin(), headers=_auth()
    )
    assert response.status_code == 503


def test_put_manual_day_skips_completion_without_repo(admin: Any) -> None:
    """No prayer repo on state skips completion; the pin still lands."""
    admin.app.state.prayer_repo = None
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days"] = []
    _write_raw(admin.dest, raw)
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=_full_pin(), headers=_auth()
    )
    assert response.status_code == 200
    assert response.json()["date"] == "2026-05-01"


def test_delete_manual_day_inline_non_list_503(admin: Any) -> None:
    """A non-list inline manual_days fails deletes safe to 503."""
    raw = _raw(admin.dest)
    raw["schedule"]["manual_days"] = "oops"
    _write_raw(admin.dest, raw)
    response = admin.client.delete(
        "/api/config/manual-days/2026-04-01", headers=_auth()
    )
    assert response.status_code == 503


def test_delete_manual_day_file_write_fails_503(admin: Any, monkeypatch: Any) -> None:
    """A pins-file write fault mid-delete is 503 (staging kept intact)."""
    import muhideen.api.admin as admin_module

    _set_pins_ref(admin.dest, "pins.json")
    (admin.dest.parent / "pins.json").write_text(
        json.dumps([{"date": "2026-05-01", **_full_pin()}], indent=2) + "\n"
    )

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk fault during pins write")

    monkeypatch.setattr(admin_module, "_atomic_write_json_list", _boom)
    response = admin.client.delete(
        "/api/config/manual-days/2026-05-01", headers=_auth()
    )
    assert response.status_code == 503


def test_validate_manual_days_locates_failing_index(admin: Any) -> None:
    """A bare array names the failing pin's index, not just the body."""
    pins = [
        {"date": "2026-04-01", **_full_pin()},
        {"date": "2026-05-01", "fajr": "05:58"},
    ]
    response = admin.client.post(
        "/api/config/manual-days/validate", json=pins, headers=_auth()
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", 1],
            "msg": "no buffer row for 2026-05-01 — sync first or send a full-day pin",
            "type": "value_error",
        }
    ]


def test_pins_completion_dateless_error_unit() -> None:
    """Completion failures without a date locate the bare body."""
    from muhideen.adapters.file_models import ManualDay  # noqa: PLC2701
    from muhideen.api.admin import _pins_completion_422  # noqa: PLC2701
    from muhideen.core.errors import ConfigError

    pin = ManualDay.model_validate({"date": "2026-05-01", **_full_pin()})
    exc = _pins_completion_422(
        [pin], ConfigError("day has missing markers, somehow"), single=False
    )
    assert exc.status_code == 422
    assert exc.detail[0]["loc"] == ["body"]
