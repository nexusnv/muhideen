"""Per-section config PATCH + GET + validate.

Spec S2 over the Task 1 foundation (token gate, shared lock, 422 shape):
all-optional partial PATCH per section with raw-dict surgery, fixed merge
depth (deep-merge jakim/aladhan, full-replace overrides/iqamah_rules),
``restart_required`` only for tz and effective-sync-client changes, public
section GETs with ``$schemaVersion`` on the full file, and gated dry-run
validate POSTs. Pinned FakeClock + TestClient over a pristine tmp copy of
the example config (null coords, jakim provider).
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
ADMIN_TOKEN = "config-admin-token-0123456789abcdef"


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
    """Gated app: Bearer token required for PATCH and validate POSTs."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest)


@pytest.fixture
def disabled(tmp_path: Path) -> Any:
    """App booted with no token: writes/validates are 503, reads stay 200."""
    built = _build_app(tmp_path, None)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest)


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


def _section(admin: Any, name: str) -> dict[str, Any]:
    response = admin.client.get(f"/api/config/{name}")
    assert response.status_code == 200
    result = response.json()
    assert isinstance(result, dict)
    return result


# --- masjid ---


def test_masjid_patch_round_trip_restart_on_tz_change(admin: Any) -> None:
    """Timezone flip persists and demands a restart; name-only does not."""
    response = admin.client.patch(
        "/api/config/masjid",
        json={"name": "Masjid Baru", "timezone": "Asia/Jakarta"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "restart_required": True}
    assert _section(admin, "masjid") == {
        "name": "Masjid Baru",
        "timezone": "Asia/Jakarta",
    }
    on_disk = json.loads(admin.dest.read_text())["masjid"]
    assert on_disk == {"name": "Masjid Baru", "timezone": "Asia/Jakarta"}

    rename = admin.client.patch(
        "/api/config/masjid", json={"name": "Masjid Lama"}, headers=_auth()
    )
    assert rename.json() == {"ok": True, "restart_required": False}
    assert _section(admin, "masjid")["timezone"] == "Asia/Jakarta"


def test_masjid_blank_name_422_and_disk_untouched(admin: Any) -> None:
    """Whitespace-only free text is 422 and never touches disk."""
    before = admin.dest.read_bytes()
    response = admin.client.patch(
        "/api/config/masjid", json={"name": "   "}, headers=_auth()
    )
    _assert_422(response, "name")
    assert admin.dest.read_bytes() == before


def test_masjid_unknown_timezone_422_never_500(admin: Any) -> None:
    """ZoneInfo failures map to 422 [{loc, msg}], never 500."""
    response = admin.client.patch(
        "/api/config/masjid", json={"timezone": "Not/AZone"}, headers=_auth()
    )
    _assert_422(response, "timezone")


def test_masjid_unknown_key_422(admin: Any) -> None:
    """Partials fail closed on unknown keys."""
    response = admin.client.patch(
        "/api/config/masjid", json={"bogus": 1}, headers=_auth()
    )
    assert response.status_code == 422


def test_masjid_fixture_bodies(admin: Any) -> None:
    """The shipped 200/422 samples behave as labelled."""
    ok_body = _fixture("admin-masjid-200.json")
    ok_response = admin.client.patch(
        "/api/config/masjid", json=ok_body, headers=_auth()
    )
    assert ok_response.status_code == 200
    assert ok_response.json() == {"ok": True, "restart_required": False}
    bad_response = admin.client.patch(
        "/api/config/masjid", json=_fixture("admin-masjid-422.json"), headers=_auth()
    )
    _assert_422(bad_response, "name")


# --- schedule ---


def test_schedule_hot_reload_knob_no_restart(admin: Any) -> None:
    """Non-sync edits hot-reload: restart_required stays false."""
    response = admin.client.patch(
        "/api/config/schedule", json={"boundary_countdown": True}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert _section(admin, "schedule")["boundary_countdown"] is True


def test_schedule_provider_switch_needs_restart(admin: Any) -> None:
    """Changing sync_provider is an effective-sync-client change."""
    response = admin.client.patch(
        "/api/config/schedule", json={"sync_provider": "none"}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": True}
    assert _section(admin, "schedule")["sync_provider"] == "none"


def test_schedule_aladhan_deep_merge_preserves_method(admin: Any) -> None:
    """One-level deep merge: {aladhan:{base_url}} keeps method; no restart."""
    response = admin.client.patch(
        "/api/config/schedule",
        json={"aladhan": {"base_url": "https://aladhan.example.com/v1"}},
        headers=_auth(),
    )
    assert response.json() == {"ok": True, "restart_required": False}
    aladhan = _section(admin, "schedule")["aladhan"]
    assert aladhan == {"base_url": "https://aladhan.example.com/v1", "method": 17}


def test_schedule_aladhan_edit_while_aladhan_needs_restart(admin: Any) -> None:
    """aladhan.* edits restart only while the effective provider is aladhan."""
    switch = admin.client.patch(
        "/api/config/schedule",
        json={"sync_provider": "aladhan", "lat": 3.07, "lon": 101.69},
        headers=_auth(),
    )
    assert switch.json() == {"ok": True, "restart_required": True}
    edit = admin.client.patch(
        "/api/config/schedule", json={"aladhan": {"method": 2}}, headers=_auth()
    )
    assert edit.json() == {"ok": True, "restart_required": True}
    assert _section(admin, "schedule")["aladhan"]["method"] == 2


def test_schedule_provider_switch_without_required_fields_422(admin: Any) -> None:
    """Sibling invariant: aladhan needs lat+lon in the same merged result."""
    before = admin.dest.read_bytes()
    response = admin.client.patch(
        "/api/config/schedule", json={"sync_provider": "aladhan"}, headers=_auth()
    )
    assert response.status_code == 422
    assert admin.dest.read_bytes() == before


def test_schedule_blank_free_text_422(admin: Any) -> None:
    """zone, jakim.zone, and manual_days_file reject whitespace-only."""
    zone = admin.client.patch(
        "/api/config/schedule", json={"zone": "   "}, headers=_auth()
    )
    _assert_422(zone, "zone")
    jakim = admin.client.patch(
        "/api/config/schedule", json={"jakim": {"zone": "  "}}, headers=_auth()
    )
    _assert_422(jakim, "jakim", "zone")
    ref = admin.client.patch(
        "/api/config/schedule", json={"manual_days_file": "  "}, headers=_auth()
    )
    _assert_422(ref, "manual_days_file")


def test_schedule_fixture_bodies(admin: Any) -> None:
    """The shipped 200/422 samples behave as labelled (base has no coords)."""
    ok_response = admin.client.patch(
        "/api/config/schedule",
        json=_fixture("admin-schedule-200.json"),
        headers=_auth(),
    )
    assert ok_response.status_code == 200
    bad_response = admin.client.patch(
        "/api/config/schedule",
        json=_fixture("admin-schedule-422.json"),
        headers=_auth(),
    )
    assert bad_response.status_code == 422


# --- timing ---


def test_timing_overrides_replace_whole_map(admin: Any) -> None:
    """Overrides are full-replace: omitted keys are dropped, not merged."""
    response = admin.client.patch(
        "/api/config/timing",
        json={"countdown_before_adhan_overrides": {"isha": 8}},
        headers=_auth(),
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert _section(admin, "timing")["countdown_before_adhan_overrides"] == {"isha": 8}


def test_timing_boundary_override_key_422(admin: Any) -> None:
    """Boundary markers cannot carry countdown overrides (Settings rule)."""
    response = admin.client.patch(
        "/api/config/timing",
        json={"countdown_before_adhan_overrides": {"imsak": 5}},
        headers=_auth(),
    )
    _assert_422(response, "countdown_before_adhan_overrides", "imsak")


def test_timing_unknown_override_key_422(admin: Any) -> None:
    """Unknown override keys are 422, never 500."""
    response = admin.client.patch(
        "/api/config/timing",
        json={"countdown_before_adhan_overrides": {"fajr2": 5}},
        headers=_auth(),
    )
    _assert_422(response, "countdown_before_adhan_overrides", "fajr2")


def test_timing_fixed_rule_without_time_422(admin: Any) -> None:
    """A fixed-mode rule without fixed_time is 422."""
    rules = _section(admin, "timing")["iqamah_rules"]
    rules[0] = {"prayer": "fajr", "mode": "fixed", "delay_minutes": 15}
    response = admin.client.patch(
        "/api/config/timing", json={"iqamah_rules": rules}, headers=_auth()
    )
    assert response.status_code == 422


def test_timing_partial_iqamah_rules_422(admin: Any) -> None:
    """iqamah_rules is all-or-nothing: one rule never covers six prayers."""
    response = admin.client.patch(
        "/api/config/timing",
        json={
            "iqamah_rules": [
                {
                    "prayer": "fajr",
                    "mode": "delay",
                    "delay_minutes": 15,
                    "fixed_time": None,
                }
            ]
        },
        headers=_auth(),
    )
    assert response.status_code == 422


# --- theme ---


def test_theme_patch_round_trip_no_restart(admin: Any) -> None:
    """Theme knobs merge per-knob and never need a restart."""
    response = admin.client.patch(
        "/api/config/theme", json={"palette": "midnight"}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    theme = _section(admin, "theme")
    assert theme["palette"] == "midnight"
    assert theme["font"] == "outfit"


def test_theme_null_knob_422(admin: Any) -> None:
    """Global theme knobs are non-nullable: explicit null is 422."""
    response = admin.client.patch(
        "/api/config/theme", json={"palette": None}, headers=_auth()
    )
    _assert_422(response, "palette")


# --- adhan-audio ---


def test_adhan_audio_volume_round_trip_no_restart(admin: Any) -> None:
    """Audio knobs persist and never need a restart."""
    response = admin.client.patch(
        "/api/config/adhan-audio", json={"volume": 42}, headers=_auth()
    )
    assert response.json() == {"ok": True, "restart_required": False}
    assert _section(admin, "adhan-audio")["volume"] == 42


def test_adhan_audio_muted_boundary_422(admin: Any) -> None:
    """Muted prayers are prayer-only (Settings rule), even when disabled."""
    response = admin.client.patch(
        "/api/config/adhan-audio",
        json={"muted_prayers": ["fajr", "imsak"]},
        headers=_auth(),
    )
    assert response.status_code == 422


def test_adhan_audio_quiet_hours_unpaired_422(admin: Any) -> None:
    """Quiet bounds are paired-or-null."""
    response = admin.client.patch(
        "/api/config/adhan-audio",
        json={"quiet_hours_start": "22:00"},
        headers=_auth(),
    )
    assert response.status_code == 422


def test_adhan_audio_file_ignored_when_disabled(admin: Any) -> None:
    """Only the file knob skips validation while audio is disabled."""
    response = admin.client.patch(
        "/api/config/adhan-audio",
        json={"enabled": False, "file": "  "},
        headers=_auth(),
    )
    assert response.status_code == 200


def test_adhan_audio_bad_file_when_enabled_422(admin: Any) -> None:
    """Escaping file paths are 422 once audio is enabled."""
    response = admin.client.patch(
        "/api/config/adhan-audio",
        json={"enabled": True, "file": "../evil.mp3"},
        headers=_auth(),
    )
    assert response.status_code == 422


# --- GET ---


def test_get_full_config_carries_schema_version_alias(admin: Any) -> None:
    """Full-file GET serializes by_alias so $schemaVersion round-trips."""
    response = admin.client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["$schemaVersion"] == 1
    assert "schema_version" not in body
    assert body["masjid"]["name"] == "Masjid An-Nur"


@pytest.mark.parametrize(
    "section", ["masjid", "schedule", "timing", "theme", "adhan-audio"]
)
def test_get_each_section_200(admin: Any, section: str) -> None:
    """Every section GET serves its parsed section."""
    assert admin.client.get(f"/api/config/{section}").status_code == 200


def test_get_unknown_section_404(admin: Any) -> None:
    """Unknown section names are 404, never 422 or 500."""
    response = admin.client.get("/api/config/playlists")
    assert response.status_code == 404


def test_config_gets_are_public(disabled: Any) -> None:
    """Reads stay public while gated writes/validates answer 503."""
    assert disabled.client.get("/api/config").status_code == 200
    assert disabled.client.get("/api/config/masjid").status_code == 200


# --- validate ---


def test_validate_full_file_ok(admin: Any) -> None:
    """A full-file candidate matching disk validates dry-run."""
    candidate = admin.client.get("/api/config").json()
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert json.loads(admin.dest.read_text())["masjid"]["name"] == "Masjid An-Nur"


def test_validate_full_file_rejects_snake_case_version(admin: Any) -> None:
    """ConfigFile validates by alias only: schema_version is rejected."""
    candidate = admin.client.get("/api/config").json()
    candidate["schema_version"] = candidate.pop("$schemaVersion")
    response = admin.client.post(
        "/api/config/validate", json=candidate, headers=_auth()
    )
    assert response.status_code == 422


def test_validate_section_merges_server_side(admin: Any) -> None:
    """Section validate merges over disk: the sibling rule still applies."""
    bad = admin.client.post(
        "/api/config/schedule/validate",
        json={"sync_provider": "aladhan"},
        headers=_auth(),
    )
    assert bad.status_code == 422
    good = admin.client.post(
        "/api/config/schedule/validate",
        json={"boundary_countdown": True},
        headers=_auth(),
    )
    assert good.json() == {"ok": True}
    assert _section(admin, "schedule")["boundary_countdown"] is False


def test_validate_never_writes(admin: Any) -> None:
    """Dry-run validate leaves the file byte-identical."""
    before = admin.dest.read_bytes()
    admin.client.post(
        "/api/config/masjid/validate", json={"name": "Dry Run"}, headers=_auth()
    )
    assert admin.dest.read_bytes() == before


# --- auth gating ---


def test_patch_without_token_is_401_with_challenge(admin: Any) -> None:
    """PATCH without a Bearer is 401, never 404 or 403."""
    response = admin.client.patch("/api/config/masjid", json={"name": "X"})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_disabled_patch_and_validate_are_503(disabled: Any) -> None:
    """No boot token: writes and dry-run validates are 503, even gated."""
    assert (
        disabled.client.patch(
            "/api/config/masjid", json={"name": "X"}, headers=_auth("any-token")
        ).status_code
        == 503
    )
    assert (
        disabled.client.post(
            "/api/config/masjid/validate",
            json={"name": "X"},
            headers=_auth("any-token"),
        ).status_code
        == 503
    )
