"""Manual-days GET/PUT/DELETE + validate.

Spec S3 over the Task 1 foundation (token gate, shared lock, 422 shape):
the effective-pins read stays public while PUT/DELETE/validate are gated
writes. Source routing follows ``schedule.manual_days_file`` (resolved via
``resolve_pins_path`` / ``manual_days_file_for_config``): inline by
default, pins file when the ref is set. A set-but-absent ref is 503 on
GET/DELETE (never an empty ``pins: []``) and is created by PUT (mkdir +
list-atomic-write). The path date is authoritative, PUT is upsert (200,
never 409), and buffer-completion failures carry the trap message.
Pinned FakeClock + TestClient over a pristine tmp copy of the example
config (mirrors the ``test_no_admin`` fixture shape).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"
FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "manual-days-admin-token-0123456789ab"
ZONE = "SGR01"

FULL_PIN = {
    "date": "2026-05-01",
    "imsak": "05:48",
    "fajr": "05:58",
    "syuruq": "07:05",
    "dhuha": "07:33",
    "dhuhr": "13:15",
    "asr": "16:30",
    "maghrib": "19:15",
    "isha": "20:30",
}
TRAP = "no buffer row for 2026-05-02 — sync first or send a full-day pin"


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
    buffer = tmp_path / "buffer.json"
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(buffer, cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
        admin_token=admin_token,
    )
    return SimpleNamespace(app=create_app(deps), dest=dest, buffer=buffer)


@pytest.fixture
def admin(tmp_path: Path) -> Any:
    """Gated app: Bearer token required for manual-days writes."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client, dest=built.dest, buffer=built.buffer, app=built.app
        )


@pytest.fixture
def disabled(tmp_path: Path) -> Any:
    """App booted with no token: writes are 503, public reads stay 200."""
    built = _build_app(tmp_path, None)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client, dest=built.dest, buffer=built.buffer, app=built.app
        )


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


def _raw_manual_days(dest: Path) -> list[Any]:
    return json.loads(dest.read_text())["schedule"]["manual_days"]


def _seed_buffer_row(buffer: Path, day: date) -> None:
    """Store one provider row so a partial pin for ``day`` can complete."""
    from muhideen.adapters.file_config import FilePrayerRepo
    from muhideen.core.values import PrayerDay, ScheduleSource

    repo = FilePrayerRepo(buffer, [])
    repo.save_day(
        PrayerDay(
            date=day,
            zone=ZONE,
            imsak=time(5, 48),
            fajr=time(5, 58),
            syuruq=time(7, 5),
            dhuha=time(7, 33),
            dhuhr=time(13, 15),
            asr=time(16, 30),
            maghrib=time(19, 15),
            isha=time(20, 30),
            source=ScheduleSource.JAKIM,
            fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )


def _set_pins_ref(admin: Any, ref: str, pins: list[Any]) -> Path:
    """Establish file mode: seed the pins file, then point the ref at it."""
    pins_path = admin.dest.parent / ref
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(json.dumps(pins, indent=2) + "\n")
    response = admin.client.patch(
        "/api/config/schedule", json={"manual_days_file": ref}, headers=_auth()
    )
    assert response.status_code == 200, response.json()
    return pins_path


def _clear_inline(admin: Any) -> None:
    """Migration first step: DELETE each inline date (no bulk clear)."""
    for entry in list(_raw_manual_days(admin.dest)):
        response = admin.client.delete(
            f"/api/config/manual-days/{entry['date']}", headers=_auth()
        )
        assert response.status_code == 200, response.json()


# --- GET (public read) ---


def test_get_returns_inline_source_and_seed_pin(admin: Any) -> None:
    """GET serves {source, pins}: inline by default with the seed pin."""
    response = admin.client.get("/api/config/manual-days")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "inline"
    assert body["pins"] == [
        {
            "date": "2026-04-01",
            "imsak": "05:48",
            "fajr": "05:58",
            "syuruq": "07:05",
            "dhuha": "07:33",
            "dhuhr": "13:15",
            "asr": "16:30",
            "maghrib": "19:15",
            "isha": "20:30",
        }
    ]


def test_get_is_public(disabled: Any) -> None:
    """The effective-pins read stays public while writes are disabled."""
    response = disabled.client.get("/api/config/manual-days")
    assert response.status_code == 200
    assert response.json()["source"] == "inline"


def test_get_file_source_after_ref(admin: Any) -> None:
    """GET reports file source once the ref routes writes to the pins file."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    assert pins_path.exists()
    response = admin.client.get("/api/config/manual-days")
    assert response.status_code == 200
    assert response.json() == {"source": "file", "pins": [FULL_PIN]}


def test_get_ref_set_but_absent_is_503_never_empty(admin: Any) -> None:
    """A set-but-absent ref is 503 with a hand-fix detail, never pins: []."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    pins_path.unlink()
    response = admin.client.get("/api/config/manual-days")
    assert response.status_code == 503
    assert "pins.json" in response.json()["detail"]
    assert response.json() != {"source": "file", "pins": []}


# --- PUT (gated upsert) ---


def test_put_create_full_pin_returns_200(admin: Any) -> None:
    """PUT a new date stores it inline (200) and the row round-trips."""
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json=_fixture("admin-pin-200.json"),
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json() == FULL_PIN
    stored = _raw_manual_days(admin.dest)
    assert len(stored) == 2
    assert stored[1] == FULL_PIN


def test_put_upsert_replace_returns_200_never_409(admin: Any) -> None:
    """PUT over an existing date replaces it (200); duplicates never 409."""
    first = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth()
    )
    assert first.status_code == 200
    changed = {**FULL_PIN, "fajr": "06:05"}
    second = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=changed, headers=_auth()
    )
    assert second.status_code == 200
    assert second.json()["fajr"] == "06:05"
    stored = _raw_manual_days(admin.dest)
    assert [entry["date"] for entry in stored].count("2026-05-01") == 1
    assert [entry for entry in stored if entry["date"] == "2026-05-01"][0] == changed


def test_put_path_date_is_authoritative_422_on_mismatch(admin: Any) -> None:
    """A body date must match the path date; mismatches fail, disk kept."""
    before = admin.dest.read_bytes()
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json={**FULL_PIN, "date": "2026-05-02"},
        headers=_auth(),
    )
    _assert_422(response, "date")
    assert admin.dest.read_bytes() == before


def test_put_date_only_and_bad_hhmm_are_422(admin: Any) -> None:
    """Zero markers and malformed HH:MM are structural 422s; disk kept."""
    before = admin.dest.read_bytes()
    _assert_422(
        admin.client.put("/api/config/manual-days/2026-05-02", json={}, headers=_auth())
    )
    bad = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json=_fixture("admin-pin-422.json"),
        headers=_auth(),
    )
    _assert_422(bad, "fajr")
    assert admin.dest.read_bytes() == before


def test_put_unknown_key_422(admin: Any) -> None:
    """Bodies fail closed: unknown keys are 422."""
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json={**FULL_PIN, "bogus": 1},
        headers=_auth(),
    )
    assert response.status_code == 422


def test_put_partial_without_buffer_row_is_422_trap(admin: Any) -> None:
    """A partial pin with no buffer row fails with the named trap detail."""
    response = admin.client.put(
        "/api/config/manual-days/2026-05-02",
        json={"date": "2026-05-02", "fajr": "05:58"},
        headers=_auth(),
    )
    detail = _assert_422(response, "date")
    assert TRAP in [entry["msg"] for entry in detail]


def test_put_partial_with_buffer_row_succeeds(admin: Any) -> None:
    """A partial pin completes against a synced row and stores as-is."""
    _seed_buffer_row(admin.buffer, date(2026, 5, 3))
    response = admin.client.put(
        "/api/config/manual-days/2026-05-03",
        json={"date": "2026-05-03", "fajr": "05:58"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["fajr"] == "05:58"
    assert response.json()["imsak"] is None


def test_put_unordered_complete_pin_422(admin: Any) -> None:
    """A complete but unordered pin fails buffer-completion, disk kept."""
    before = admin.dest.read_bytes()
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json={**FULL_PIN, "fajr": "21:00"},
        headers=_auth(),
    )
    assert response.status_code == 422
    assert admin.dest.read_bytes() == before


def test_put_bad_path_date_422(admin: Any) -> None:
    """A non-calendar path date is a 422, never a stored row."""
    response = admin.client.put(
        "/api/config/manual-days/2026-13-99", json=FULL_PIN, headers=_auth()
    )
    assert response.status_code == 422


def test_pin_fixture_bodies(admin: Any) -> None:
    """The shipped 200/422 pin samples behave as labelled."""
    created = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json=_fixture("admin-pin-200.json"),
        headers=_auth(),
    )
    assert created.status_code == 200
    assert created.json()["date"] == "2026-05-01"
    bad = admin.client.put(
        "/api/config/manual-days/2026-05-01",
        json=_fixture("admin-pin-422.json"),
        headers=_auth(),
    )
    _assert_422(bad, "fajr")


# --- file-mode writes ---


def test_put_file_mode_writes_pins_file_not_config(admin: Any) -> None:
    """With the ref set, PUT routes to the pins file; config stays inline-free."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [])
    before = admin.dest.read_bytes()
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth()
    )
    assert response.status_code == 200
    assert json.loads(pins_path.read_text()) == [FULL_PIN]
    assert _raw_manual_days(admin.dest) == []
    assert admin.dest.read_bytes() == before
    assert admin.client.get("/api/config/manual-days").json() == {
        "source": "file",
        "pins": [FULL_PIN],
    }


def test_put_creates_absent_pins_file_with_mkdir(admin: Any) -> None:
    """Ref set but file absent: PUT creates it, mkdir-ing missing parents."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "sub/dir/pins.json", [FULL_PIN])
    pins_path.unlink()
    shutil.rmtree(admin.dest.parent / "sub")
    assert not (admin.dest.parent / "sub").exists()
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth()
    )
    assert response.status_code == 200
    assert json.loads(pins_path.read_text()) == [FULL_PIN]


def test_put_file_mode_upsert_replaces_200(admin: Any) -> None:
    """File-mode PUT over an existing date replaces it (200, never 409)."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    changed = {**FULL_PIN, "isha": "20:45"}
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=changed, headers=_auth()
    )
    assert response.status_code == 200
    assert json.loads(pins_path.read_text()) == [changed]


def test_put_inline_plus_file_exclusive_422(admin: Any) -> None:
    """Inline pins plus a set ref is exclusive: PUT is 422, disk kept."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    raw = json.loads(admin.dest.read_text())
    raw["schedule"]["manual_days"] = [FULL_PIN]
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    before_pins = pins_path.read_bytes()
    response = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth()
    )
    _assert_422(response, "manual_days_file")
    assert pins_path.read_bytes() == before_pins


# --- DELETE (gated) ---


def test_delete_removes_and_unknown_404(admin: Any) -> None:
    """DELETE removes the date; repeating it (or unknown dates) is 404."""
    assert (
        admin.client.put(
            "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth()
        ).status_code
        == 200
    )
    gone = admin.client.delete("/api/config/manual-days/2026-05-01", headers=_auth())
    assert gone.status_code == 200
    assert gone.json() == {"ok": True}
    assert [entry["date"] for entry in _raw_manual_days(admin.dest)] == ["2026-04-01"]
    assert (
        admin.client.delete(
            "/api/config/manual-days/2026-05-01", headers=_auth()
        ).status_code
        == 404
    )
    assert (
        admin.client.delete(
            "/api/config/manual-days/2026-06-01", headers=_auth()
        ).status_code
        == 404
    )


def test_delete_ref_set_but_absent_is_503(admin: Any) -> None:
    """No pins file to delete from: DELETE is 503 like GET."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    pins_path.unlink()
    response = admin.client.delete(
        "/api/config/manual-days/2026-05-01", headers=_auth()
    )
    assert response.status_code == 503


def test_delete_file_mode_removes_row(admin: Any) -> None:
    """File-mode DELETE shrinks the pins file and leaves the config alone."""
    _clear_inline(admin)
    pins_path = _set_pins_ref(admin, "pins.json", [FULL_PIN])
    before = admin.dest.read_bytes()
    response = admin.client.delete(
        "/api/config/manual-days/2026-05-01", headers=_auth()
    )
    assert response.status_code == 200
    assert json.loads(pins_path.read_text()) == []
    assert admin.dest.read_bytes() == before
    assert (
        admin.client.delete(
            "/api/config/manual-days/2026-05-01", headers=_auth()
        ).status_code
        == 404
    )


# --- validate (gated dry-run) ---


def test_validate_full_pin_200(admin: Any) -> None:
    """A complete pin validates dry-run: 200, nothing written."""
    before = admin.dest.read_bytes()
    response = admin.client.post(
        "/api/config/manual-days/validate", json=[FULL_PIN], headers=_auth()
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert admin.dest.read_bytes() == before


def test_validate_partial_without_buffer_row_422_trap(admin: Any) -> None:
    """A partial pin with no buffer row fails with the same trap message."""
    response = admin.client.post(
        "/api/config/manual-days/validate",
        json=[{"date": "2026-05-02", "fajr": "05:58"}],
        headers=_auth(),
    )
    detail = _assert_422(response)
    assert TRAP in [entry["msg"] for entry in detail]


def test_validate_partial_with_buffer_row_200(admin: Any) -> None:
    """A partial pin completes against a synced row: 200 dry-run."""
    _seed_buffer_row(admin.buffer, date(2026, 5, 4))
    response = admin.client.post(
        "/api/config/manual-days/validate",
        json=[{"date": "2026-05-04", "fajr": "05:58"}],
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_validate_structural_422(admin: Any) -> None:
    """Bad HH:MM, date-only pins, and duplicate dates are structural 422s."""
    bad_time = admin.client.post(
        "/api/config/manual-days/validate",
        json=[{"date": "2026-05-01", "fajr": "99:99"}],
        headers=_auth(),
    )
    assert bad_time.status_code == 422
    date_only = admin.client.post(
        "/api/config/manual-days/validate",
        json=[{"date": "2026-05-01"}],
        headers=_auth(),
    )
    assert date_only.status_code == 422
    dupes = admin.client.post(
        "/api/config/manual-days/validate",
        json=[FULL_PIN, FULL_PIN],
        headers=_auth(),
    )
    detail = _assert_422(dupes)
    assert any("duplicate" in entry["msg"] for entry in detail)


def test_validate_fixture_bodies(admin: Any) -> None:
    """The shipped pin samples validate as labelled when array-wrapped."""
    good = admin.client.post(
        "/api/config/manual-days/validate",
        json=[_fixture("admin-pin-200.json")],
        headers=_auth(),
    )
    assert good.status_code == 200
    bad = admin.client.post(
        "/api/config/manual-days/validate",
        json=[_fixture("admin-pin-422.json")],
        headers=_auth(),
    )
    assert bad.status_code == 422


# --- gating ---


def test_manual_days_writes_require_token(admin: Any) -> None:
    """Bearer-less (or wrong) writes are 401 with the challenge."""
    assert (
        admin.client.put(
            "/api/config/manual-days/2026-05-01", json=FULL_PIN
        ).status_code
        == 401
    )
    bad = admin.client.put(
        "/api/config/manual-days/2026-05-01", json=FULL_PIN, headers=_auth("wrong")
    )
    assert bad.status_code == 401
    assert bad.headers.get("www-authenticate") == "Bearer"
    assert admin.client.delete("/api/config/manual-days/2026-05-01").status_code == 401
    assert (
        admin.client.post(
            "/api/config/manual-days/validate", json=[FULL_PIN]
        ).status_code
        == 401
    )


def test_manual_days_writes_disabled_without_boot_token(disabled: Any) -> None:
    """No boot token: writes and dry-run validates are 503 even with Bearer."""
    assert (
        disabled.client.put(
            "/api/config/manual-days/2026-05-01",
            json=FULL_PIN,
            headers=_auth("any-token"),
        ).status_code
        == 503
    )
    assert (
        disabled.client.delete(
            "/api/config/manual-days/2026-05-01", headers=_auth("any-token")
        ).status_code
        == 503
    )
    assert (
        disabled.client.post(
            "/api/config/manual-days/validate",
            json=[FULL_PIN],
            headers=_auth("any-token"),
        ).status_code
        == 503
    )
