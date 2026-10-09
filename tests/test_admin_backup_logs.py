"""Backup export/restore + service logs (spec §4).

Export (gated ``GET /api/backup/export``) zips the live installation:
``muhideen.json`` + the pins file when the ref is set + the prayer
buffer byte-copied as-is when present (even corrupt — backup is not
validation) + ``manifest.json`` (``{files:[{path,size_bytes,sha256}],
exported_at}``) + media binaries under ``media/``. Media sizes sum
before archiving (over ``50MB`` uncompressed → ``413``); the zip
streams, never buffering every binary in memory; every member path is
relative, never absolute.

Restore (gated multipart ``POST /api/backup/restore``, same bounded
streaming as media, ``≤50MB`` upload) hardens zip-slip (absolute,
``..``, symlink, and drive-letter members rejected), validates
everything (config via ``ConfigFile``, pins via pins-file semantics,
buffer schema, staged pins completion against the STAGED buffer — not
the live one — media relpaths plus the total-size cap) BEFORE touching
live disk, then applies through a same-filesystem staging dir
(``<config-dir>/.restore-*.tmp``, removed on success, kept on failure
until the next restore) with ordered renames (config → pins →
buffer), pre-rename backups for rollback, and additive per-file
tmp+rename media (orphans persist, never deleted).

Logs (gated ``GET /api/logs?lines=``) tails the unit journal via
argv-only ``journalctl`` with a 5s timeout (``501`` on
timeout/unavailable), default ``200`` lines, ``1-1000`` else ``422``.
Pinned FakeClock + TestClient over a pristine tmp copy of the example
config (mirrors the ``test_admin_media`` fixture shape).
"""

from __future__ import annotations

import io
import json
import shutil
import stat
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "backup-admin-token-0123456789abcdef"


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
    """File-backed app over a pristine example copy plus media and buffer."""
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
    buffer_path = tmp_path / "buffer.json"
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(buffer_path, cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
        admin_token=admin_token,
    )
    app = create_app(deps)
    return SimpleNamespace(app=app, dest=dest, media=media, buffer=buffer_path)


@pytest.fixture
def admin(tmp_path: Path) -> Any:
    """Gated app: Bearer token required for export/restore/logs."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client, dest=built.dest, media=built.media, buffer=built.buffer
        )


@pytest.fixture
def disabled(tmp_path: Path) -> Any:
    """App booted with no token: gated endpoints are 503, reads stay 200."""
    built = _build_app(tmp_path, None)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client, dest=built.dest, media=built.media, buffer=built.buffer
        )


def _staging_dirs(dest: Path) -> list[Path]:
    """Same-filesystem restore staging dirs next to the live config."""
    return sorted(dest.parent.glob(".restore-*.tmp"))


def _zip_bytes(members: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    """Build an in-memory zip; optionally one symlink member (Unix attrs)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "media/target.png")
    return buf.getvalue()


def _restore(
    client: TestClient,
    payload: bytes,
    *,
    filename: str = "backup.zip",
    headers: dict[str, str] | None = None,
) -> Any:
    """Multipart restore upload (mirrors the media upload call shape)."""
    return client.post(
        "/api/backup/restore",
        files={"file": (filename, payload, "application/zip")},
        headers=headers if headers is not None else _auth(),
    )


def _live_config_bytes(dest: Path) -> bytes:
    return dest.read_bytes()


# --- Export: gating. ---


def test_export_gated(admin: Any, disabled: Any) -> None:
    """No boot token → 503 even with Bearer; wrong/missing Bearer → 401."""
    assert disabled.client.get("/api/backup/export").status_code == 503
    assert disabled.client.get("/api/backup/export", headers=_auth()).status_code == 503
    assert admin.client.get("/api/backup/export").status_code == 401
    bad = {"Authorization": "Bearer wrong-token"}
    assert admin.client.get("/api/backup/export", headers=bad).status_code == 401


# --- Export: content. ---


def test_export_streams_zip_with_relative_paths_only(admin: Any) -> None:
    """Zip holds config + manifest + media; every member path is relative."""
    (admin.media / "adhan.mp3").write_bytes(b"ID3\x04\x00\x00" + bytes(32))
    (admin.media / "playlists").mkdir(exist_ok=True)
    (admin.media / "playlists" / "slide.png").write_bytes(bytes(64))
    admin.buffer.write_bytes(b'{"days": {}}')

    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert "muhideen-backup-" in disposition and disposition.endswith('.zip"')

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        assert "muhideen.json" in names
        assert "prayer_buffer.json" in names
        assert "manifest.json" in names
        assert "media/adhan.mp3" in names
        assert "media/playlists/slide.png" in names
        for name in names:
            assert name and not name.startswith("/"), name
            assert "\\" not in name, name
        assert zf.read("muhideen.json") == _live_config_bytes(admin.dest)
        assert zf.read("prayer_buffer.json") == admin.buffer.read_bytes()
        assert zf.read("media/adhan.mp3").startswith(b"ID3")

        manifest = json.loads(zf.read("manifest.json"))
        assert set(manifest) == {"files", "exported_at"}
        datetime.fromisoformat(manifest["exported_at"])
        entries = {entry["path"]: entry for entry in manifest["files"]}
        assert set(entries) >= {
            "muhideen.json",
            "prayer_buffer.json",
            "media/adhan.mp3",
        }
        for entry in manifest["files"]:
            assert set(entry) == {"path", "size_bytes", "sha256"}
            assert entry["size_bytes"] == len(zf.read(entry["path"]))


def test_export_includes_pins_file_when_ref_set(admin: Any) -> None:
    """A set pins ref adds ``pins.json`` (byte copy of the resolved file)."""
    raw = json.loads(admin.dest.read_text())
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = "pins.json"
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    pins = [{"date": "2026-05-01", "fajr": "05:58"}]
    (admin.dest.parent / "pins.json").write_text(json.dumps(pins, indent=2) + "\n")

    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "pins.json" in zf.namelist()
        assert json.loads(zf.read("pins.json")) == pins


def test_export_copies_corrupt_buffer_byte_for_byte(admin: Any) -> None:
    """Backup is not validation: a corrupt buffer exports as-is, still 200."""
    corrupt = b"{not json at all\x00\xff"
    admin.buffer.write_bytes(corrupt)
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.read("prayer_buffer.json") == corrupt


def test_export_413_when_media_exceeds_cap(admin: Any, monkeypatch: Any) -> None:
    """Media sizes sum BEFORE archiving: over the cap aborts with 413."""
    import muhideen.api.admin as admin_module

    monkeypatch.setattr(admin_module, "BACKUP_MEDIA_CAP_BYTES", 1024)
    (admin.media / "big.mp3").write_bytes(bytes(2048))
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 413
    assert not (admin.dest.parent / "big.zip").exists()


# --- Restore: gating + upload envelope. ---


def test_restore_gated(admin: Any, disabled: Any) -> None:
    """No boot token → 503; missing/wrong Bearer → 401 (never 404)."""
    payload = _zip_bytes({"muhideen.json": b"{}"})
    assert disabled.client.post("/api/backup/restore").status_code == 503
    assert _restore(disabled.client, payload, headers=_auth()).status_code == 503
    assert _restore(admin.client, payload, headers={}).status_code == 401
    bad = {"Authorization": "Bearer wrong-token"}
    assert _restore(admin.client, payload, headers=bad).status_code == 401


def test_restore_rejects_non_zip_and_enforces_upload_cap(
    admin: Any, monkeypatch: Any
) -> None:
    """Non-zip bytes are 422; uploads over the cap abort with 413."""
    import muhideen.api.admin as admin_module

    before = _live_config_bytes(admin.dest)
    response = _restore(admin.client, b"definitely not a zip")
    assert response.status_code == 422
    assert _live_config_bytes(admin.dest) == before
    assert _staging_dirs(admin.dest) != []  # kept for forensics

    monkeypatch.setattr(admin_module, "BACKUP_UPLOAD_CAP_BYTES", 64)
    response = _restore(admin.client, bytes(512))
    assert response.status_code == 413
    assert _live_config_bytes(admin.dest) == before


# --- Restore: zip-slip hardening (validate before touching disk). ---


@pytest.mark.parametrize(
    "member",
    [
        "/abs.json",
        "../escape.json",
        "media/../../escape.png",
        "C:drive.json",
        "..\\windows.json",
    ],
)
def test_restore_rejects_zip_slip_members(admin: Any, member: str) -> None:
    """Absolute/``..``/drive-letter members are 422; live disk untouched."""
    before = _live_config_bytes(admin.dest)
    payload = _zip_bytes(
        {"muhideen.json": _live_config_bytes(admin.dest), member: b"evil"}
    )
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert _live_config_bytes(admin.dest) == before
    assert list(admin.media.rglob("*")) == []
    assert _staging_dirs(admin.dest) != []  # kept for forensics


def test_restore_rejects_symlink_member(admin: Any) -> None:
    """Symlink members are 422 even when their link path looks relative."""
    before = _live_config_bytes(admin.dest)
    payload = _zip_bytes(
        {"muhideen.json": _live_config_bytes(admin.dest)},
        symlink="media/link.png",
    )
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert _live_config_bytes(admin.dest) == before


# --- Restore: validate-all-before-touch. ---


def test_restore_validates_everything_before_touching_disk(admin: Any) -> None:
    """A bad pins member fails the whole restore: config + media untouched."""
    before = _live_config_bytes(admin.dest)
    raw = json.loads(before.decode())
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = "pins.json"
    staged_config = json.dumps(raw, indent=2).encode()
    payload = _zip_bytes(
        {
            "muhideen.json": staged_config,
            "pins.json": b"[{not valid",
            "media/new.png": bytes(16),
        }
    )
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert _live_config_bytes(admin.dest) == before
    assert not (admin.media / "new.png").exists()
    assert list(admin.media.rglob("*")) == []


def test_restore_checks_staged_pins_against_staged_buffer(admin: Any) -> None:
    """Pins completion uses the STAGED buffer, never the live one.

    The live buffer holds a row for the pinned date, but the staged
    buffer is empty — a partial pin must still fail with the pre-sync
    trap instead of passing against the live row.
    """
    from muhideen.adapters.file_config import _day_to_entry  # noqa: PLC2701
    from muhideen.core.values import PrayerDay, ScheduleSource  # noqa: PLC2701

    live_day = PrayerDay(
        date=datetime(2026, 5, 1).date(),
        zone="SGR01",
        imsak=datetime(2026, 5, 1, 5, 48).time(),
        fajr=datetime(2026, 5, 1, 5, 58).time(),
        syuruq=datetime(2026, 5, 1, 7, 5).time(),
        dhuha=datetime(2026, 5, 1, 7, 33).time(),
        dhuhr=datetime(2026, 5, 1, 13, 15).time(),
        asr=datetime(2026, 5, 1, 16, 30).time(),
        maghrib=datetime(2026, 5, 1, 19, 15).time(),
        isha=datetime(2026, 5, 1, 20, 30).time(),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2026, 5, 1, 2, 0, tzinfo=KL),
    )
    admin.buffer.write_text(
        json.dumps(
            {
                "$schemaVersion": 1,
                "zone": "SGR01",
                "fetched_at": "2026-05-01T02:00:00+08:00",
                "days": {"2026-05-01": _day_to_entry(live_day)},
            },
            indent=2,
        )
        + "\n"
    )
    raw = json.loads(_live_config_bytes(admin.dest).decode())
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = "pins.json"
    payload = _zip_bytes(
        {
            "muhideen.json": json.dumps(raw, indent=2).encode(),
            "pins.json": json.dumps([{"date": "2026-05-01", "fajr": "05:58"}]).encode(),
            "prayer_buffer.json": b'{"days": {}}',
        }
    )
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert "no buffer row for 2026-05-01" in response.json()["detail"][0]["msg"]
    assert _live_config_bytes(admin.dest) != payload  # config file untouched
    assert _staging_dirs(admin.dest) != []


# --- Restore: apply (staging, order, rollback, additive media). ---


def test_restore_round_trip_applies_and_clears_staging(admin: Any) -> None:
    """Export → tweak → restore lands the tweak; staging is gone after."""
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        members = {
            name: zf.read(name) for name in zf.namelist() if name != "manifest.json"
        }
    raw = json.loads(members["muhideen.json"].decode())
    raw["masjid"]["name"] = "Restored Masjid"
    members["muhideen.json"] = json.dumps(raw, indent=2).encode()
    members["media/added.png"] = bytes(32)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    restored = _restore(admin.client, buf.getvalue())
    assert restored.status_code == 200
    assert restored.json() == {"ok": True}
    served = admin.client.get("/api/config/masjid")
    assert served.status_code == 200
    assert served.json()["name"] == "Restored Masjid"
    assert (admin.media / "added.png").read_bytes() == bytes(32)
    assert _staging_dirs(admin.dest) == []


def test_restore_media_is_additive_and_orphans_persist(admin: Any) -> None:
    """Same-relpath media overwrites, unlisted orphans are never deleted."""
    (admin.media / "playlists").mkdir(exist_ok=True)
    (admin.media / "orphan.mp3").write_bytes(b"ID3" + bytes(8))
    (admin.media / "playlists" / "keep.png").write_bytes(b"old-bytes")
    payload = _zip_bytes(
        {
            "muhideen.json": _live_config_bytes(admin.dest),
            "media/playlists/keep.png": b"new-bytes",
        }
    )
    assert _restore(admin.client, payload).status_code == 200
    assert (admin.media / "playlists" / "keep.png").read_bytes() == b"new-bytes"
    assert (admin.media / "orphan.mp3").is_file()


def test_restore_rolls_back_on_mid_apply_failure(admin: Any, monkeypatch: Any) -> None:
    """Failing the buffer rename rolls the config rename back (503)."""
    import os

    import muhideen.api.admin as admin_module

    before = _live_config_bytes(admin.dest)
    live_buffer = admin.buffer
    live_buffer.write_bytes(b'{"days": {}}')
    raw = json.loads(before.decode())
    raw["masjid"]["name"] = "Doomed Rename"
    payload = _zip_bytes(
        {
            "muhideen.json": json.dumps(raw, indent=2).encode(),
            "prayer_buffer.json": b'{"days": {}}',
        }
    )
    real_replace = os.replace

    def _fail_on_buffer(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if str(dst) == str(live_buffer):
            raise OSError("disk fault during buffer rename")
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(admin_module.os, "replace", _fail_on_buffer)
    response = _restore(admin.client, payload)
    assert response.status_code == 503
    assert _live_config_bytes(admin.dest) == before
    assert _staging_dirs(admin.dest) != []  # kept for forensics


def test_restore_next_attempt_cleans_previous_failure_staging(admin: Any) -> None:
    """A kept-for-forensics staging dir is removed by the next restore."""
    before = _live_config_bytes(admin.dest)
    bad = _zip_bytes(
        {"muhideen.json": _live_config_bytes(admin.dest), "/abs.json": b"evil"}
    )
    assert _restore(admin.client, bad).status_code == 422
    assert _staging_dirs(admin.dest) != []
    good = _zip_bytes({"muhideen.json": _live_config_bytes(admin.dest)})
    assert _restore(admin.client, good).status_code == 200
    assert _live_config_bytes(admin.dest) == before
    assert _staging_dirs(admin.dest) == []


# --- Logs. ---


class _Completed:
    """Minimal stand-in for ``subprocess.CompletedProcess`` (stdout only)."""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.args: Any = None


def _fake_journal(
    monkeypatch: Any, *, stdout: bytes = b"line1\nline2\n", returncode: int = 0
) -> dict[str, Any]:
    """Patch ``subprocess.run``; capture argv for the argv-only assertion."""
    seen: dict[str, Any] = {}

    def _run(argv: Any, **kwargs: Any) -> _Completed:
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        assert kwargs.get("shell", False) is False
        return _Completed(stdout, returncode)

    monkeypatch.setattr(subprocess, "run", _run)
    return seen


def test_logs_gated(admin: Any, disabled: Any) -> None:
    """No boot token → 503; missing/wrong Bearer → 401."""
    assert disabled.client.get("/api/logs").status_code == 503
    assert admin.client.get("/api/logs").status_code == 401
    bad = {"Authorization": "Bearer wrong-token"}
    assert admin.client.get("/api/logs", headers=bad).status_code == 401


def test_logs_default_and_range_validation(admin: Any, monkeypatch: Any) -> None:
    """Default 200 lines via argv (no shell); 1-1000 pass, else 422."""
    seen = _fake_journal(monkeypatch)
    response = admin.client.get("/api/logs", headers=_auth())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert seen["argv"] == ["journalctl", "-u", "muhideen", "--no-pager", "-n", "200"]
    assert response.text == "line1\nline2\n"

    seen = _fake_journal(monkeypatch, stdout=b"one\n")
    assert admin.client.get("/api/logs", params={"lines": 1}, headers=_auth()).text == (
        "one\n"
    )
    assert seen["argv"][-1] == "1"
    response = admin.client.get("/api/logs", params={"lines": 1000}, headers=_auth())
    assert response.status_code == 200
    assert seen["argv"][-1] == "1000"

    for bad_value in ("0", "1001", "-5", "many", ""):
        response = admin.client.get(
            "/api/logs", params={"lines": bad_value}, headers=_auth()
        )
        assert response.status_code == 422, bad_value


def test_logs_fail_soft_501(admin: Any, monkeypatch: Any) -> None:
    """Timeout, missing binary, and journald errors are all 501, never 500."""

    def _timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

    monkeypatch.setattr(subprocess, "run", _timeout)
    response = admin.client.get("/api/logs", headers=_auth())
    assert response.status_code == 501

    def _missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("journalctl")

    monkeypatch.setattr(subprocess, "run", _missing)
    assert admin.client.get("/api/logs", headers=_auth()).status_code == 501

    _fake_journal(monkeypatch, stdout=b"no journal", returncode=1)
    response = admin.client.get("/api/logs", headers=_auth())
    assert response.status_code == 501
    assert "detail" in response.json()


def test_logs_percent_encoded_lines_rejected(admin: Any) -> None:
    """A crafted ``lines`` path segment never reaches the unit (routing 404)."""
    response = admin.client.get(
        f"/api/logs/{quote('1;reboot', safe='')}", headers=_auth()
    )
    assert response.status_code == 404
