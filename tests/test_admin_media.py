"""Media upload/list/delete (spec §4).

Kind routes the destination (``adhan`` → media root, ``image`` →
``playlists/`` with the subdir ensured); basenames flatten (no subdirs
v1) and reject empty/NUL/``?``/``#``/trailing-``/``/backslash/drive/
absolute/``..``/overlong with 422. Size caps (``≤5MB`` image,
``≤10MB`` audio) gate on upload bytes via bounded streaming reads
(oversize → 413); images are Pillow open-verified + re-encoded (EXIF
stripped, ``MAX_IMAGE_PIXELS`` guard, format preserved), audio gets
the weak MP3 frame-sync/ID3 check, kind/ext mismatches → 422.
Overwrite answers 200 (new → 201); DELETE is unconditional (dangling
refs allowed, no reference scan), unknown → 404. Pinned FakeClock +
TestClient over a pristine tmp copy of the example config (mirrors the
``test_admin_playlists`` fixture shape).
"""

from __future__ import annotations

import asyncio
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
ADMIN_TOKEN = "media-admin-token-0123456789abcdef"

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
        yield SimpleNamespace(client=client, dest=built.dest, media=built.media)


@pytest.fixture
def disabled(tmp_path: Path) -> Any:
    """App booted with no token: writes are 503, the list stays 200."""
    built = _build_app(tmp_path, None)
    with TestClient(built.app) as client:
        yield SimpleNamespace(client=client, dest=built.dest, media=built.media)


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


def _jpeg_bytes(*, exif: bool = False) -> bytes:
    """Small valid JPEG, optionally carrying an EXIF Make tag."""
    from PIL import Image

    img = Image.new("RGB", (16, 16), (255, 0, 0))
    buf = io.BytesIO()
    if exif:
        tag = Image.Exif()
        tag[0x010F] = "Maker"
        img.save(buf, format="JPEG", exif=tag)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes() -> bytes:
    """Small valid PNG."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 255, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _webp_bytes() -> bytes:
    """Small valid WebP."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 0, 255)).save(buf, format="WEBP")
    return buf.getvalue()


def _mp3_bytes(*, magic: str = "id3") -> bytes:
    """Minimal MP3-looking payload: ID3 header or a frame-sync word."""
    if magic == "id3":
        return b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(128)
    return b"\xff\xfb\x90\x00" + bytes(128)


def _upload(
    client: TestClient,
    filename: str,
    payload: bytes,
    kind: str | None,
    *,
    content_type: str = "application/octet-stream",
    headers: dict[str, str] | None = None,
) -> Any:
    """Multipart upload; ``kind=None`` omits the kind field entirely."""
    data = {} if kind is None else {"kind": kind}
    return client.post(
        "/api/media",
        files={"file": (filename, payload, content_type)},
        data=data,
        headers=headers if headers is not None else _auth(),
    )


def test_list_media_starts_empty_and_public(admin: Any) -> None:
    """GET /api/media is public (no token) and lists [] on a fresh dir."""
    response = admin.client.get("/api/media")
    assert response.status_code == 200
    assert response.json() == []


def test_upload_adhan_routes_to_media_root(admin: Any) -> None:
    """``kind=adhan`` stores at the media root with the normalized relpath."""
    payload = _mp3_bytes()
    response = _upload(admin.client, "adhan.mp3", payload, "adhan")
    assert response.status_code == 201
    assert response.json() == {"path": "adhan.mp3"}
    assert (admin.media / "adhan.mp3").read_bytes() == payload
    listing = admin.client.get("/api/media").json()
    assert {"path": "adhan.mp3", "size_bytes": len(payload)} in listing


def test_upload_image_routes_to_playlists_subdir(admin: Any) -> None:
    """``kind=image`` stores under ``playlists/``, ensuring the subdir."""
    assert not (admin.media / "playlists").exists()
    response = _upload(admin.client, "Pic.PNG", _png_bytes(), "image")
    assert response.status_code == 201
    assert response.json() == {"path": "playlists/Pic.PNG"}
    assert (admin.media / "playlists" / "Pic.PNG").is_file()


def test_upload_flattens_client_subdir(admin: Any) -> None:
    """No subdirs v1: ``subdir/file.jpg`` flattens to the basename."""
    response = _upload(admin.client, "subdir/file.jpg", _jpeg_bytes(), "image")
    assert response.status_code == 201
    assert response.json() == {"path": "playlists/file.jpg"}
    assert not (admin.media / "playlists" / "subdir").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "   ",
        "a?.mp3",
        "a#.mp3",
        "mydir/",
        "..",
        "/abs.mp3",
        "C:evil.mp3",
        "..\\evil.mp3",
        "../escape.mp3",
        "a" * 300 + ".mp3",
    ],
)
def test_upload_rejects_bad_basename(admin: Any, filename: str) -> None:
    """Reject-list basenames (incl. overlong → ENAMETOOLONG) are 422."""
    response = _upload(admin.client, filename, _mp3_bytes(), "adhan")
    _assert_422(response, "file")


def test_upload_windows_absolute_arrives_flattened(admin: Any) -> None:
    """``C:\\evil.mp3`` stores as ``evil.mp3`` (multipart strips the drive).

    The client stack (httpx escaping + parser basename) delivers only
    ``evil.mp3`` as the filename, so the server validates and stores
    that clean basename — never the drive prefix. Raw backslashes that
    do reach the server are rejected (see the store-level test below).
    """
    response = _upload(admin.client, "C:\\evil.mp3", _mp3_bytes(), "adhan")
    assert response.status_code == 201
    assert response.json() == {"path": "evil.mp3"}


def test_basename_rejects_raw_backslash_and_drive() -> None:
    """Store-level: backslash/drive/absolute names reject before any write."""
    from muhideen.adapters.media_store import sanitize_upload_basename

    for raw in ("C:\\evil.mp3", "..\\evil.mp3", "C:evil.mp3", "/abs.mp3"):
        with pytest.raises(ValueError):
            sanitize_upload_basename(raw)
    assert sanitize_upload_basename("subdir/file.jpg") == "file.jpg"


def test_upload_rejects_nul_basename(admin: Any) -> None:
    """NUL in the client filename is 422 (store-level, client-safe)."""
    from muhideen.adapters.media_store import sanitize_upload_basename

    with pytest.raises(ValueError, match="NUL"):
        sanitize_upload_basename("a\x00b.mp3")


def test_upload_enforces_size_caps(admin: Any) -> None:
    """Oversize aborts the bounded read with 413 before any decode."""
    big_image = b"x" * (IMAGE_CAP + 1)
    response = _upload(admin.client, "big.png", big_image, "image")
    assert response.status_code == 413
    assert not (admin.media / "playlists" / "big.png").exists()
    big_audio = b"y" * (AUDIO_CAP + 1)
    response = _upload(admin.client, "big.mp3", big_audio, "adhan")
    assert response.status_code == 413
    assert not (admin.media / "big.mp3").exists()


def test_upload_at_exact_audio_cap_passes(admin: Any) -> None:
    """Exactly ``≤10MB`` of ID3 MP3 passes the gate (boundary, not off-by-one)."""
    payload = b"ID3" + bytes(AUDIO_CAP - 3)
    response = _upload(admin.client, "exact.mp3", payload, "adhan")
    assert response.status_code == 201
    assert response.json() == {"path": "exact.mp3"}


def test_bounded_read_aborts_without_draining_stream() -> None:
    """The chunked gate stops at cap+1: the backing stream is not drained."""
    from fastapi import UploadFile

    from muhideen.adapters.media_store import (
        MediaTooLargeError,
        read_upload_bounded,
    )

    backing = io.BytesIO(b"z" * (IMAGE_CAP + 2 * 65536))
    upload = UploadFile(file=backing, filename="big.png")
    with pytest.raises(MediaTooLargeError):
        asyncio.run(read_upload_bounded(upload, IMAGE_CAP))
    assert len(backing.read()) > 0


def test_upload_reencodes_image_stripping_exif(admin: Any) -> None:
    """Pillow re-encode strips EXIF while the stored file stays a JPEG."""
    from PIL import Image

    raw = _jpeg_bytes(exif=True)
    assert Image.open(io.BytesIO(raw)).getexif()
    response = _upload(admin.client, "photo.jpg", raw, "image")
    assert response.status_code == 201
    stored = (admin.media / "playlists" / "photo.jpg").read_bytes()
    reopened = Image.open(io.BytesIO(stored))
    assert reopened.format == "JPEG"
    assert not reopened.getexif()


def test_upload_max_image_pixels_guard(admin: Any, monkeypatch: Any) -> None:
    """A tiny ``MAX_IMAGE_PIXELS`` turns a small PNG into a 422 bomb reject."""
    monkeypatch.setattr("PIL.Image.MAX_IMAGE_PIXELS", 10)
    response = _upload(admin.client, "pic.png", _png_bytes(), "image")
    _assert_422(response, "file")


@pytest.mark.parametrize(
    ("filename", "maker", "expected_format"),
    [
        ("a.jpg", _jpeg_bytes, "JPEG"),
        ("b.png", _png_bytes, "PNG"),
        ("c.webp", _webp_bytes, "WEBP"),
    ],
)
def test_upload_preserves_image_format(
    admin: Any, filename: str, maker: Any, expected_format: str
) -> None:
    """Re-encoded output keeps the Pillow-detected format per extension."""
    from PIL import Image

    response = _upload(admin.client, filename, maker(), "image")
    assert response.status_code == 201
    stored = (admin.media / "playlists" / filename).read_bytes()
    assert Image.open(io.BytesIO(stored)).format == expected_format


def test_upload_rejects_undecodable_image(admin: Any) -> None:
    """A ``.png`` Pillow cannot open is 422 (extension alone never suffices)."""
    response = _upload(admin.client, "fake.png", b"not an image at all", "image")
    _assert_422(response, "file")


def test_upload_rejects_non_image_content_for_image_ext(admin: Any) -> None:
    """A GIF renamed ``.jpg`` is 422 (content must decode to jpg/png/webp)."""
    gif = (
        b"GIF89a\x10\x00\x10\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x10\x00"
        b"\x10\x00\x00\x02\x19\x8c\x8f\xa9\xcb\xed\x0f\xa3\x9c\xb4\xda"
        b"\x8b\xb3\xde\xbc\xfb\x0f\x00\x3b"
    )
    response = _upload(admin.client, "anim.jpg", gif, "image")
    _assert_422(response, "file")


def test_upload_accepts_mp3_frame_sync_without_id3(admin: Any) -> None:
    """Frame-sync MP3s pass the weak v1 check without an ID3 header."""
    response = _upload(admin.client, "live.mp3", _mp3_bytes(magic="sync"), "adhan")
    assert response.status_code == 201
    assert response.json() == {"path": "live.mp3"}


def test_upload_rejects_non_mp3_audio(admin: Any) -> None:
    """Non-MP3 bytes named ``.mp3`` are 422 (magic check, no full decode)."""
    response = _upload(admin.client, "nope.mp3", b"definitely not audio", "adhan")
    _assert_422(response, "file")


@pytest.mark.parametrize(
    ("filename", "kind", "payload"),
    [
        ("song.mp3", "image", None),
        ("pic.png", "adhan", None),
        ("pic.png", "video", None),
    ],
)
def test_upload_rejects_kind_mismatch(
    admin: Any, filename: str, kind: str, payload: bytes | None
) -> None:
    """Mismatched kind/ext (and unknown kinds) are 422, nothing written."""
    body = _mp3_bytes() if filename.endswith(".mp3") else _png_bytes()
    response = _upload(admin.client, filename, body, kind)
    _assert_422(response, "kind")
    assert admin.client.get("/api/media").json() == []


def test_upload_requires_file_and_kind(admin: Any) -> None:
    """Missing binary or missing kind field is 422 (FastAPI required shape)."""
    missing_file = admin.client.post(
        "/api/media", data={"kind": "adhan"}, headers=_auth()
    )
    assert missing_file.status_code == 422
    missing_kind = _upload(admin.client, "x.mp3", _mp3_bytes(), None)
    assert missing_kind.status_code == 422


def test_upload_overwrite_returns_200(admin: Any) -> None:
    """Second upload of a relpath replaces bytes and answers 200 (new → 201)."""
    first = _mp3_bytes()
    assert _upload(admin.client, "dup.mp3", first, "adhan").status_code == 201
    second = _mp3_bytes(magic="sync")
    assert second != first
    response = _upload(admin.client, "dup.mp3", second, "adhan")
    assert response.status_code == 200
    assert response.json() == {"path": "dup.mp3"}
    assert (admin.media / "dup.mp3").read_bytes() == second
    assert len(admin.client.get("/api/media").json()) == 1


def test_delete_is_unconditional_dangling_allowed(admin: Any) -> None:
    """DELETE never scans references: a playlist-referenced file deletes fine."""
    assert _upload(admin.client, "slide.png", _png_bytes(), "image").status_code == 201
    created = admin.client.post(
        "/api/playlists",
        json={
            "id": "media-refs",
            "title": "Refs",
            "items": [{"image_path": "playlists/slide.png", "duration_s": 5}],
        },
        headers=_auth(),
    )
    assert created.status_code == 201
    response = admin.client.delete("/api/media/playlists/slide.png", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert not (admin.media / "playlists" / "slide.png").exists()
    detail = admin.client.get("/api/playlists/media-refs").json()
    assert detail["items"][0]["image_path"] == "playlists/slide.png"


def test_delete_unknown_is_404(admin: Any) -> None:
    """DELETE of a missing relpath is 404 (after auth, before any scan)."""
    response = admin.client.delete("/api/media/nope.mp3", headers=_auth())
    assert response.status_code == 404


def test_delete_rejects_escape_and_strips_legacy_prefix(admin: Any) -> None:
    """Percent-decoded ``..`` is 422; a legacy ``media/`` prefix strips."""
    assert _upload(admin.client, "strip.mp3", _mp3_bytes(), "adhan").status_code == 201
    escaped = admin.client.delete("/api/media/%2E%2E/muhideen.json", headers=_auth())
    _assert_422(escaped, "path")
    assert (admin.media / "strip.mp3").is_file()
    stripped = admin.client.delete("/api/media/media/strip.mp3", headers=_auth())
    assert stripped.status_code == 200
    assert not (admin.media / "strip.mp3").exists()


def test_media_writes_gated(disabled: Any) -> None:
    """No boot token: upload + delete are 503, the list stays public 200."""
    assert disabled.client.get("/api/media").status_code == 200
    assert (
        _upload(disabled.client, "x.mp3", _mp3_bytes(), "adhan", headers={}).status_code
        == 503
    )
    assert disabled.client.delete("/api/media/x.mp3").status_code == 503


def test_media_writes_need_bearer(admin: Any) -> None:
    """Missing/wrong Bearer on writes is 401 (never 404/503 with a token set)."""
    assert (
        _upload(admin.client, "x.mp3", _mp3_bytes(), "adhan", headers={}).status_code
        == 401
    )
    bad = {"Authorization": "Bearer wrong-token"}
    assert (
        _upload(admin.client, "x.mp3", _mp3_bytes(), "adhan", headers=bad).status_code
        == 401
    )
    assert admin.client.delete("/api/media/x.mp3", headers=bad).status_code == 401
