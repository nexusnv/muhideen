"""Admin backup/restore/export + repo-helper error branches (spec §4).

Second-layer coverage over ``tests/test_admin_backup_logs.py``: export
fault modes (missing pins ref, unreadable buffer, zip-build failure),
restore hardening branches (encrypted/duplicate/oversize/unknown
members, manifest shapes, staged config shapes), staged-atomic apply
branches (EXDEV fallback, rollback fault tolerance, pins promotion,
media faults), upload-envelope branches, staging cleanup, the
blocked-media-dir mount path, and file-config helper edges. Pinned
FakeClock + TestClient over a pristine tmp copy of the example config
(mirrors the ``test_admin_backup_logs`` fixture shape).
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "edge-restore-token-0123456789abcdef"


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
    """Gated app: Bearer token required for export/restore."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client,
            dest=built.dest,
            media=built.media,
            buffer=built.buffer,
            app=built.app,
        )


def _zip_bytes(
    members: dict[str, bytes],
    *,
    encrypted: str | None = None,
    extra_dirs: list[str] | None = None,
) -> bytes:
    """Build an in-memory zip; optionally one encrypted member or dir entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if encrypted is not None:
            info = zipfile.ZipInfo(encrypted)
            info.flag_bits |= 0x1
            zf.writestr(info, b"secret")
        for dirname in extra_dirs or []:
            zf.writestr(zipfile.ZipInfo(dirname + "/"), b"")
    return buf.getvalue()


def _restore(
    client: TestClient,
    payload: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Multipart restore upload (mirrors the media upload call shape)."""
    return client.post(
        "/api/backup/restore",
        files={"file": ("backup.zip", payload, "application/zip")},
        headers=headers if headers is not None else _auth(),
    )


def _live_config(admin: Any) -> bytes:
    return admin.dest.read_bytes()


def _config_with_ref(admin: Any, ref: str) -> bytes:
    raw = json.loads(_live_config(admin).decode())
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = ref
    return (json.dumps(raw, indent=2) + "\n").encode()


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


# --- Export: fault modes. ---


def test_export_pins_ref_missing_file_503(admin: Any) -> None:
    """A set-but-absent pins ref fails the export safe to 503."""
    admin.dest.write_text(_config_with_ref(admin, "pins.json").decode())
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 503
    assert "pins.json" in response.json()["detail"]


def test_export_buffer_unreadable_503(admin: Any, monkeypatch: Any) -> None:
    """A present-but-unreadable buffer fails the export safe to 503."""
    admin.buffer.write_bytes(b'{"days": {}}')
    real_read_bytes = Path.read_bytes

    def _fault(self: Path) -> bytes:
        if self == admin.buffer:
            raise OSError("disk fault during buffer read")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _fault)
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 503


def test_export_without_prayer_repo_skips_buffer(admin: Any) -> None:
    """No prayer repo on state falls back to the config sibling (200)."""
    admin.app.state.prayer_repo = object()
    response = admin.client.get("/api/backup/export", headers=_auth())
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "prayer_buffer.json" not in zf.namelist()


def test_export_zip_build_failure_cleans_tmp(admin: Any, monkeypatch: Any) -> None:
    """A zip-build fault propagates (500) with the temp archive removed."""
    before = set(Path(tempfile.gettempdir()).glob("muhideen-backup-*.zip"))

    def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("disk fault during zip build")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", _boom)
    with pytest.raises(OSError, match="zip build"):
        admin.client.get("/api/backup/export", headers=_auth())
    assert set(Path(tempfile.gettempdir()).glob("muhideen-backup-*.zip")) == before


# --- Restore: upload envelope. ---


def test_restore_declared_size_over_cap_413(admin: Any, monkeypatch: Any) -> None:
    """A declared Content-Length past the cap aborts before streaming."""
    import muhideen.api.admin as admin_module

    monkeypatch.setattr(admin_module, "BACKUP_UPLOAD_CAP_BYTES", 64)
    monkeypatch.setattr(admin_module, "UPFRONT_SLACK_BYTES", 0)
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before})
    response = _restore(admin.client, payload)
    assert response.status_code == 413
    assert _live_config(admin) == before


def test_restore_garbage_content_length_falls_through(admin: Any) -> None:
    """A non-numeric Content-Length cannot gate; hardening still decides."""
    payload = _zip_bytes({"muhideen.json": _live_config(admin)})
    response = _restore(
        admin.client, payload, headers={**_auth(), "content-length": "nope"}
    )
    assert response.status_code == 200


# --- Restore: member hardening. ---


def test_restore_rejects_encrypted_member(admin: Any) -> None:
    """Encrypted members are 422 even with a valid config member."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before}, encrypted="muhideen.json")
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert _live_config(admin) == before


def test_restore_rejects_unknown_dir_member(admin: Any) -> None:
    """Unknown directory members are 422 (only media/ may be a dir)."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before}, extra_dirs=["otherdir", "media"])
    assert _restore(admin.client, payload).status_code == 422
    assert _live_config(admin) == before


def test_restore_rejects_duplicate_member(admin: Any) -> None:
    """A doubled member name is 422; live disk stays untouched."""
    before = _live_config(admin)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("muhideen.json", before)
        zf.writestr("muhideen.json", before)
    assert _restore(admin.client, buf.getvalue()).status_code == 422
    assert _live_config(admin) == before


def test_restore_rejects_oversize_config_member(admin: Any, monkeypatch: Any) -> None:
    """Non-media members past the sanity cap are 422 before any read."""
    import muhideen.api.admin as admin_module

    monkeypatch.setattr(admin_module, "_BACKUP_NON_MEDIA_CAP_BYTES", 4)
    payload = _zip_bytes({"muhideen.json": _live_config(admin)})
    assert _restore(admin.client, payload).status_code == 422


@pytest.mark.parametrize("member", ["pins.json", "prayer_buffer.json", "manifest.json"])
def test_restore_rejects_oversize_small_members(
    admin: Any, monkeypatch: Any, member: str
) -> None:
    """Each non-media slot enforces the sanity cap independently."""
    import muhideen.api.admin as admin_module

    monkeypatch.setattr(admin_module, "_BACKUP_NON_MEDIA_CAP_BYTES", 100)
    payload = _zip_bytes({"muhideen.json": b"{}", member: bytes(200)})
    assert _restore(admin.client, payload).status_code == 422


@pytest.mark.parametrize("member", ["", ".", "a\\b.json"])
def test_restore_rejects_unhardenable_names(admin: Any, member: str) -> None:
    """Empty, dot, and backslash member names are 422 (zip-slip gate)."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, member: b"evil"})
    assert _restore(admin.client, payload).status_code == 422
    assert _live_config(admin) == before


def test_restore_rejects_bad_media_relpath(admin: Any) -> None:
    """A media member failing media normalization is 422 at its relpath."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, "media/a?.png": bytes(16)})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "media/a?.png"]
    assert _live_config(admin) == before


def test_restore_media_total_over_cap_413(admin: Any, monkeypatch: Any) -> None:
    """The hardened media total aborts with 413 before touching disk."""
    import muhideen.api.admin as admin_module

    monkeypatch.setattr(admin_module, "BACKUP_MEDIA_CAP_BYTES", 10)
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, "media/big.mp3": bytes(16)})
    assert _restore(admin.client, payload).status_code == 413
    assert _live_config(admin) == before
    assert list(admin.media.rglob("*")) == []


def test_restore_rejects_unknown_member(admin: Any) -> None:
    """Unknown top-level members are 422 (allowlist, never lenient)."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, "evil.json": b"{}"})
    assert _restore(admin.client, payload).status_code == 422
    assert _live_config(admin) == before


def test_restore_missing_config_422(admin: Any) -> None:
    """A zip without muhideen.json is 422 (media alone never applies)."""
    payload = _zip_bytes({"media/a.mp3": bytes(16)})
    assert _restore(admin.client, payload).status_code == 422
    assert list(admin.media.rglob("*")) == []


@pytest.mark.parametrize(
    "manifest",
    [b"not json", b"[1, 2]", b'{"files": {}}'],
)
def test_restore_rejects_bad_manifest(admin: Any, manifest: bytes) -> None:
    """Non-JSON, non-object, and files-less manifests are 422."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, "manifest.json": manifest})
    assert _restore(admin.client, payload).status_code == 422
    assert _live_config(admin) == before


# --- Restore: staged config shapes. ---


@pytest.mark.parametrize(
    ("config", "match"),
    [
        (b"\xff\xfe\x00bad", "UTF-8"),
        (b"{oops", "JSON"),
        (b"[1, 2]", "object"),
    ],
)
def test_restore_rejects_unparsable_config(
    admin: Any, config: bytes, match: str
) -> None:
    """Non-UTF8, non-JSON, and non-object staged configs are 422."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": config})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert match in response.json()["detail"][0]["msg"]
    assert _live_config(admin) == before


def test_restore_rejects_structurally_invalid_config(admin: Any) -> None:
    """A staged config failing ConfigFile is 422 with file-rooted locs."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": b"{}"})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "body"
    assert _live_config(admin) == before


def test_restore_rejects_settings_invalid_config(admin: Any) -> None:
    """A staged config failing Settings conversion is 422 at the archive."""
    before = _live_config(admin)
    raw = json.loads(before.decode())
    raw["timing"]["countdown_before_adhan_overrides"] = {"imsak": 5}
    payload = _zip_bytes({"muhideen.json": json.dumps(raw).encode()})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "muhideen.json"]
    assert _live_config(admin) == before


def test_restore_rejects_invalid_staged_buffer(admin: Any) -> None:
    """A staged buffer failing schema validation is 422 (config untouched)."""
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before, "prayer_buffer.json": b"not json"})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "prayer_buffer.json"]
    assert _live_config(admin) == before


def test_restore_rejects_pins_without_ref(admin: Any) -> None:
    """A staged pins file with no manual_days_file ref is 422."""
    payload = _zip_bytes({"muhideen.json": _live_config(admin), "pins.json": b"[]"})
    assert _restore(admin.client, payload).status_code == 422


# --- Restore: staged-atomic apply. ---


def test_restore_skips_non_dir_stale_staging(admin: Any) -> None:
    """Cleanup only removes .restore-*.tmp dirs; nearby names survive."""
    keeper_file = admin.dest.parent / ".restore-keep.tmp"
    keeper_file.write_bytes(b"forensics")
    keeper_dir = admin.dest.parent / ".restore-keep.other"
    keeper_dir.mkdir()
    payload = _zip_bytes({"muhideen.json": _live_config(admin)})
    assert _restore(admin.client, payload).status_code == 200
    assert keeper_file.is_file()
    assert keeper_dir.is_dir()


def test_restore_creates_absent_buffer_without_prior(admin: Any) -> None:
    """Promoting over a missing live file records no backup (200)."""
    assert not admin.buffer.exists()
    raw = json.loads(_live_config(admin).decode())
    raw["masjid"]["name"] = "Buffer Created"
    payload = _zip_bytes(
        {
            "muhideen.json": json.dumps(raw).encode(),
            "prayer_buffer.json": b'{"days": {}}',
        }
    )
    assert _restore(admin.client, payload).status_code == 200
    assert admin.buffer.is_file()
    assert json.loads(_live_config(admin).decode())["masjid"]["name"] == (
        "Buffer Created"
    )


def test_restore_exdev_falls_back_to_copy(admin: Any, monkeypatch: Any) -> None:
    """Cross-filesystem renames fall back to copy (200, config applied)."""
    import errno
    import os

    real_replace = os.replace

    def _exdev(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", _exdev)
    try:
        raw = json.loads(_live_config(admin).decode())
        raw["masjid"]["name"] = "EXDEV Masjid"
        payload = _zip_bytes({"muhideen.json": json.dumps(raw).encode()})
        response = _restore(admin.client, payload)
    finally:
        monkeypatch.setattr(os, "replace", real_replace)
    assert response.status_code == 200
    assert json.loads(_live_config(admin).decode())["masjid"]["name"] == (
        "EXDEV Masjid"
    )


def test_restore_rollback_tolerates_backup_fault(admin: Any, monkeypatch: Any) -> None:
    """A rollback copy fault logs and continues; the original error wins."""
    import muhideen.api.admin as admin_module

    before = _live_config(admin)
    (admin.media / "keep.mp3").write_bytes(b"ID3" + bytes(8))
    raw = json.loads(before.decode())
    raw["masjid"]["name"] = "Doomed Rename"
    payload = _zip_bytes(
        {
            "muhideen.json": json.dumps(raw).encode(),
            "media/new.png": bytes(16),
        }
    )
    real_copyfile = shutil.copyfile

    def _fail_on_backup(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if str(src).endswith(".bak"):
            raise OSError("disk fault during rollback copy")
        real_copyfile(src, dst, *args, **kwargs)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk fault during media write")

    monkeypatch.setattr(shutil, "copyfile", _fail_on_backup)
    monkeypatch.setattr(admin_module, "atomic_write_bytes", _boom)
    response = _restore(admin.client, payload)
    assert response.status_code == 503
    # The rollback copy fault is tolerated (warning, original error wins);
    # the staged config stays live and staging is kept for forensics.
    assert json.loads(_live_config(admin).decode())["masjid"]["name"] == (
        "Doomed Rename"
    )
    assert sorted(admin.dest.parent.glob(".restore-*.tmp")) != []


def test_restore_live_pins_corrupt_422(admin: Any) -> None:
    """A corrupt live pins file fails a ref restore at pins.json."""
    admin.dest.write_text(_config_with_ref(admin, "pins.json").decode())
    (admin.dest.parent / "pins.json").write_text("[{bad\n")
    before = _live_config(admin)
    payload = _zip_bytes({"muhideen.json": before})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "pins.json"]
    assert _live_config(admin) == before


def test_restore_live_pins_valid_full_200(admin: Any) -> None:
    """Filed full-day live pins complete without any buffer row (200)."""
    admin.dest.write_text(_config_with_ref(admin, "pins.json").decode())
    (admin.dest.parent / "pins.json").write_text(
        json.dumps([{"date": "2026-05-01", **_full_pin()}], indent=2) + "\n"
    )
    payload = _zip_bytes({"muhideen.json": _live_config(admin)})
    assert _restore(admin.client, payload).status_code == 200


def test_restore_empty_pins_skips_completion(admin: Any) -> None:
    """No pins anywhere skips completion entirely (200)."""
    raw = json.loads(_live_config(admin).decode())
    raw["schedule"]["manual_days"] = []
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    payload = _zip_bytes({"muhideen.json": _live_config(admin)})
    assert _restore(admin.client, payload).status_code == 200


def test_restore_promotes_staged_pins_file(admin: Any) -> None:
    """A staged pins file with a ref promotes to the live path (200)."""
    admin.dest.write_text(_config_with_ref(admin, "pins.json").decode())
    payload = _zip_bytes({"muhideen.json": _live_config(admin), "pins.json": b"[]"})
    assert _restore(admin.client, payload).status_code == 200
    assert (admin.dest.parent / "pins.json").read_text() == "[]"


def test_restore_media_too_long_422(admin: Any) -> None:
    """An overlong media member is 422 at its archive path (never 500)."""
    before = _live_config(admin)
    long_name = "media/" + "a" * 300 + ".png"
    payload = _zip_bytes({"muhideen.json": before, long_name: bytes(16)})
    response = _restore(admin.client, payload)
    assert response.status_code == 422
    assert _live_config(admin) == before
    assert list(admin.media.rglob("*")) == []


# --- App composition: blocked media dir. ---


def test_media_mount_blocked_by_file_serves_without_media(tmp_path: Path) -> None:
    """A media path blocked by a regular file warns; the app still boots."""
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
    blocker = tmp_path / "blocker"
    blocker.write_text("in the way")
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=blocker / "media",
        config_path=dest,
        admin_token=ADMIN_TOKEN,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/media").status_code == 200
        assert client.get("/media/nope.mp3").status_code == 404


# --- File-config helper edges. ---


def test_manual_days_file_for_config_unreadable_shapes(tmp_path: Path) -> None:
    """Missing/invalid/non-object/scheduleless configs route pins inline."""
    from muhideen.adapters.file_config import manual_days_file_for_config

    missing = tmp_path / "missing.json"
    assert manual_days_file_for_config(missing) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json\n")
    assert manual_days_file_for_config(bad) is None
    array = tmp_path / "array.json"
    array.write_text("[1, 2]\n")
    assert manual_days_file_for_config(array) is None
    nosched = tmp_path / "nosched.json"
    nosched.write_text('{"masjid": {}}\n')
    assert manual_days_file_for_config(nosched) is None


def test_settings_save_rejects_non_object_sections(admin: Any) -> None:
    """Non-object jakim/aladhan sections fail saves loudly (ConfigError)."""
    from muhideen.adapters.file_config import FileSettingsRepo
    from muhideen.core.errors import ConfigError

    repo = FileSettingsRepo(admin.dest)
    settings = repo.load()
    raw = json.loads(admin.dest.read_text())
    raw["schedule"]["jakim"] = "oops"
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    with pytest.raises(ConfigError, match="jakim"):
        repo.save(settings)
    raw["schedule"]["jakim"] = {"zone": "SGR01"}
    raw["schedule"]["aladhan"] = "oops"
    admin.dest.write_text(json.dumps(raw, indent=2) + "\n")
    with pytest.raises(ConfigError, match="aladhan"):
        repo.save(settings)


def test_playlist_from_file_rejects_bad_window() -> None:
    """Window grammar failures from file rows carry the playlist id."""
    from muhideen.adapters.file_config import _playlist_from_file  # noqa: PLC2701
    from muhideen.adapters.file_models import PlaylistFile
    from muhideen.core.errors import ConfigError

    entry = PlaylistFile.model_validate(
        {"id": "w", "title": "W", "window_start": "blah"}
    )
    with pytest.raises(ConfigError, match="playlist 'w'"):
        _playlist_from_file(entry)


def test_last_known_skips_foreign_zone_buffer_row(tmp_path: Path) -> None:
    """Buffer rows for other zones never win over the queried zone."""
    from muhideen.adapters.file_config import (  # noqa: PLC2701
        FilePrayerRepo,
        _day_to_entry,
    )
    from muhideen.core.values import PrayerDay, ScheduleSource  # noqa: PLC2701

    other = PrayerDay(
        date=datetime(2026, 4, 1).date(),
        zone="SGR02",
        imsak=datetime(2026, 4, 1, 5, 48).time(),
        fajr=datetime(2026, 4, 1, 5, 58).time(),
        syuruq=datetime(2026, 4, 1, 7, 5).time(),
        dhuha=datetime(2026, 4, 1, 7, 33).time(),
        dhuhr=datetime(2026, 4, 1, 13, 15).time(),
        asr=datetime(2026, 4, 1, 16, 30).time(),
        maghrib=datetime(2026, 4, 1, 19, 15).time(),
        isha=datetime(2026, 4, 1, 20, 30).time(),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2026, 4, 1, 2, 0, tzinfo=KL),
    )
    buffer_path = tmp_path / "buffer.json"
    buffer_path.write_text(
        json.dumps(
            {
                "$schemaVersion": 1,
                "zone": "SGR02",
                "fetched_at": "2026-04-01T02:00:00+08:00",
                "days": {"2026-04-01": _day_to_entry(other)},
            },
            indent=2,
        )
        + "\n"
    )
    repo = FilePrayerRepo(buffer_path, ())
    assert repo.last_known(datetime(2026, 4, 1).date(), "SGR01") is None
