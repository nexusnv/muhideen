"""Admin media error branches + media-store edges (spec §4).

Second-layer coverage over ``tests/test_admin_media.py``: store-level
validation edges (flattened dot names, degenerate legacy prefixes, dot
roots), Pillow content branches (BMP-as-PNG, RGBA-JPEG re-encode),
bounded-write faults (ENAMETOOLONG → 422, other OSError → 503),
content-length declaration branches, DELETE fault modes, and the static
fallback media dir. Pinned FakeClock + TestClient over a pristine tmp
copy of the example config (mirrors the ``test_admin_media`` fixture
shape).
"""

from __future__ import annotations

import io
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
ADMIN_TOKEN = "edge-media-token-0123456789abcdef"

IMAGE_CAP = 5 * 1024 * 1024
AUDIO_CAP = 10 * 1024 * 1024


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
    """File-backed app over a pristine example copy plus its media dir."""
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
    return SimpleNamespace(app=create_app(deps), dest=dest, media=media)


@pytest.fixture
def admin(tmp_path: Path) -> Any:
    """Gated app: Bearer token required for upload/delete."""
    built = _build_app(tmp_path, ADMIN_TOKEN)
    with TestClient(built.app) as client:
        yield SimpleNamespace(
            client=client, dest=built.dest, media=built.media, app=built.app
        )


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


def _jpeg_bytes() -> bytes:
    """Small valid JPEG."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


def _bmp_bytes() -> bytes:
    """Small valid BMP (decodable, but never a preserved image format)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 255, 0)).save(buf, format="BMP")
    return buf.getvalue()


def _mp3_bytes() -> bytes:
    """Minimal ID3-looking MP3 payload."""
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(128)


def _upload(
    client: TestClient,
    filename: str,
    payload: bytes,
    kind: str,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Multipart upload with the Bearer token unless headers override."""
    return client.post(
        "/api/media",
        files={"file": (filename, payload, "application/octet-stream")},
        data={"kind": kind},
        headers=headers if headers is not None else _auth(),
    )


# --- store-level validation edges ---


def test_sanitize_flattened_dot_name_422() -> None:
    """A client subdir collapsing to a dot name is 422 (store-level)."""
    from muhideen.adapters.media_store import sanitize_upload_basename

    with pytest.raises(ValueError, match="must name a file"):
        sanitize_upload_basename("subdir/.")


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("", "must name a file"),
        ("   ", "must name a file"),
        ("a\x00b.png", "NUL"),
        ("a\\b.png", "backslash"),
        ("dir/", "directory"),
        ("/abs.png", "relative"),
        ("C:evil.png", "relative"),
        ("media//x.png", "relative"),
        ("media", "must name a file"),
        ("a?.png", "'\\?' or '#'"),
        ("a#.png", "'\\?' or '#'"),
        ("../escape.png", "escapes"),
    ],
)
def test_normalize_media_rel_edges(value: str, match: str) -> None:
    """DELETE-target normalization rejects each malformed shape."""
    from muhideen.adapters.media_store import normalize_media_rel

    with pytest.raises(ValueError, match=match):
        normalize_media_rel(value)


def test_normalize_media_rel_keeps_subdirs() -> None:
    """Well-formed relatives (incl. legacy prefix) normalize, keeping depth."""
    from muhideen.adapters.media_store import normalize_media_rel

    assert normalize_media_rel("playlists/x.png") == "playlists/x.png"
    assert normalize_media_rel("media/strip.mp3") == "strip.mp3"


def test_list_media_files_missing_root(tmp_path: Path) -> None:
    """A missing media root lists empty (never 500)."""
    from muhideen.adapters.media_store import list_media_files

    assert list_media_files(tmp_path / "nope") == []


def test_reencode_rejects_bmp_content() -> None:
    """Decodable-but-unpreserved formats are ValueError (store-level)."""
    from muhideen.adapters.media_store import reencode_image

    with pytest.raises(ValueError, match="not a supported image"):
        reencode_image(_bmp_bytes(), "fake.png")


# --- upload content branches ---


def test_upload_bmp_renamed_png_422(admin: Any) -> None:
    """A BMP renamed .png is 422 (content must decode to jpg/png/webp)."""
    _assert_422(_upload(admin.client, "fake.png", _bmp_bytes(), "image"), "file")


def test_upload_declared_content_length_over_cap_413(admin: Any) -> None:
    """A Content-Length past cap + slack aborts upfront with 413."""
    headers = {
        **_auth(),
        "content-length": str(IMAGE_CAP + 1024 * 1024 + 1),
    }
    response = _upload(
        admin.client, "small.png", _jpeg_bytes(), "image", headers=headers
    )
    assert response.status_code == 413
    assert not (admin.media / "playlists" / "small.png").exists()


def test_upload_garbage_content_length_falls_through(admin: Any) -> None:
    """A non-numeric Content-Length cannot gate; the bounded read decides."""
    headers = {**_auth(), "content-length": "not-a-number"}
    response = _upload(admin.client, "ok.mp3", _mp3_bytes(), "adhan", headers=headers)
    assert response.status_code == 201
    assert response.json() == {"path": "ok.mp3"}


# --- upload/delete disk faults ---


def test_upload_rename_too_long_422(admin: Any, monkeypatch: Any) -> None:
    """ENAMETOOLONG at rename time is 422 (never 500/503)."""
    import errno
    import os

    def _too_long(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(os, "replace", _too_long)
    response = _upload(admin.client, "a.mp3", _mp3_bytes(), "adhan")
    _assert_422(response, "file")
    assert not (admin.media / "a.mp3").exists()


def test_upload_rename_disk_fault_503(admin: Any, monkeypatch: Any) -> None:
    """A non-length rename fault is 503 (scrubbed detail)."""
    import os

    def _fault(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("disk fault during media rename")

    monkeypatch.setattr(os, "replace", _fault)
    response = _upload(admin.client, "a.mp3", _mp3_bytes(), "adhan")
    assert response.status_code == 503
    assert not (admin.media / "a.mp3").exists()


def test_upload_existence_probe_fault_503(admin: Any, monkeypatch: Any) -> None:
    """A non-length fault in the pre-write probe is 503 (never 422)."""
    from pathlib import Path as PathCls

    def _fault(self: Any) -> bool:
        raise OSError("disk fault during media probe")

    monkeypatch.setattr(PathCls, "is_file", _fault)
    response = _upload(admin.client, "a.mp3", _mp3_bytes(), "adhan")
    assert response.status_code == 503


def test_delete_overlong_relpath_422(admin: Any) -> None:
    """An overlong DELETE relpath probes ENAMETOOLONG → 422 (never 404)."""
    long_name = "a" * 300 + ".mp3"
    response = admin.client.delete(f"/api/media/{long_name}", headers=_auth())
    _assert_422(response, "path")


def test_delete_unlink_fault_503(admin: Any, monkeypatch: Any) -> None:
    """A delete-time unlink fault is 503 (scrubbed detail)."""
    from pathlib import Path as PathCls

    assert _upload(admin.client, "doomed.mp3", _mp3_bytes(), "adhan").status_code == 201

    def _fault(self: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("disk fault during media delete")

    monkeypatch.setattr(PathCls, "unlink", _fault)
    response = admin.client.delete("/api/media/doomed.mp3", headers=_auth())
    assert response.status_code == 503
    assert (admin.media / "doomed.mp3").is_file()


def test_list_media_static_fallback_200(admin: Any) -> None:
    """No media dir on state falls back to the static uploads dir (200)."""
    admin.app.state.media_dir = None
    response = admin.client.get("/api/media")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
