"""Media binary store: basename sanitization, bounded reads, verify, atomic writes.

Spec §4 backing for the admin media endpoints: upload basenames are
flattened (``posixpath.basename`` — no subdirs v1) and rejected on
empty/NUL/``?``/``#``/trailing-``/``/backslash/drive/absolute/``..``
escape, with overlong names surfacing as ``ENAMETOOLONG`` mapped to
``ValueError`` (the route maps that to 422, never 500/503). Size caps
(``≤5MB`` images, ``≤10MB`` audio) gate on upload bytes during chunked
streaming reads — before Pillow decode — and oversize aborts at cap+1
without buffering the whole body. Images are Pillow open-verified and
re-encoded (EXIF stripped, ``Image.MAX_IMAGE_PIXELS`` bomb guard left
at its default, format preserved); audio gets the weak v1 MP3
frame-sync/ID3 magic check. Every write (including re-encode output)
lands via tmp-in-same-dir + rename so concurrent ``/media`` reads
never see torn binaries.
"""

from __future__ import annotations

import errno
import io
import os
import posixpath
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from muhideen.core.values import strip_legacy_media_prefix

MAX_IMAGE_BYTES = 5 * 1024 * 1024
"""Pre-encode upload cap for ``kind=image`` (gate is on upload bytes)."""

MAX_AUDIO_BYTES = 10 * 1024 * 1024
"""Upload cap for ``kind=adhan`` MP3s."""

UPFRONT_SLACK_BYTES = 1024 * 1024
"""Tolerance above the cap for the whole-request Content-Length early gate.

The request ``Content-Length`` covers multipart framing around the file
bytes, so only a claim beyond cap + slack proves oversize up front;
near-cap bodies fall through to the chunked running-total gate.
"""

_CHUNK_BYTES = 64 * 1024
"""Streaming read quantum: oversize aborts after at most cap + one chunk."""

_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp"})
"""Allowed image extensions (lowercased for the type check only)."""

_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
"""Pillow formats the re-encode preserves (content must decode to one)."""

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
"""Windows drive-letter prefix (``C:...``) — always media-relative here."""


class MediaTooLargeError(Exception):
    """Upload exceeded its kind cap during the bounded streaming read."""

    def __init__(self, cap_bytes: int) -> None:
        """Record the breached cap for the 413 wire detail."""
        super().__init__(f"upload exceeds {cap_bytes} bytes")
        self.cap_bytes = cap_bytes


def sanitize_upload_basename(filename: str | None) -> str:
    """Flatten a client upload filename to a storable basename.

    ``posixpath.basename`` drops client subdirs (``subdir/file.jpg`` →
    ``file.jpg`` — no subdirs v1); the raw name is rejected first on
    empty, NUL, ``?``/``#``, trailing ``/``, backslash, absolute or
    drive-letter, and ``..`` escape after ``normpath``. The stored name
    keeps client case (only the extension check lowercases).
    """
    if filename is None or not filename.strip():
        raise ValueError("file must have a filename")
    if "\x00" in filename:
        raise ValueError(f"filename must not contain NUL bytes: {filename!r}")
    raw = filename.strip()
    if raw.endswith("/"):
        raise ValueError(f"filename must name a file, not a directory: {filename!r}")
    if "\\" in raw:
        raise ValueError(f"filename must not contain backslashes: {filename!r}")
    if raw.startswith("/") or _DRIVE_RE.match(raw) is not None:
        raise ValueError(f"filename must be relative: {filename!r}")
    if "?" in raw or "#" in raw:
        raise ValueError(f"filename must not contain '?' or '#': {filename!r}")
    norm = posixpath.normpath(raw)
    if norm in ("", ".") or norm == ".." or norm.startswith("../"):
        raise ValueError(f"filename escapes the media root: {filename!r}")
    base = posixpath.basename(raw).strip()
    if not base or base in (".", ".."):
        raise ValueError(f"filename must name a file: {filename!r}")
    if "\x00" in base or "?" in base or "#" in base:
        raise ValueError(f"filename must not contain '?' or '#': {filename!r}")
    return base


def normalize_media_rel(value: str) -> str:
    """Normalize a media-relative path (DELETE target, listing entries).

    Same rules as the configured adhan path: relative only, no ``..``
    escape after ``normpath``, no absolute/drive-letter, no ``?``/``#``,
    no NUL, no trailing ``/``, no backslash, legacy ``media/`` prefix
    stripped. Unlike uploads this keeps subdirs (``playlists/x.png``).
    """
    if not value or not value.strip():
        raise ValueError("media path must name a file")
    if "\x00" in value:
        raise ValueError(f"media path must not contain NUL bytes: {value!r}")
    if "\\" in value:
        raise ValueError(f"media path must not contain backslashes: {value!r}")
    if value.strip().endswith("/"):
        raise ValueError(f"media path must name a file, not a directory: {value!r}")
    if value.startswith("/") or _DRIVE_RE.match(value) is not None:
        raise ValueError(f"media path must be relative: {value!r}")
    rel = strip_legacy_media_prefix(value)
    if rel.startswith("/"):
        raise ValueError(f"media path must be relative: {value!r}")
    if "?" in rel or "#" in rel:
        raise ValueError(f"media path must not contain '?' or '#': {value!r}")
    norm = posixpath.normpath(rel)
    if norm in ("", "."):
        raise ValueError(f"media path must name a file: {value!r}")
    if norm == ".." or norm.startswith("../"):
        raise ValueError(f"media path escapes the media root: {value!r}")
    return norm


def _extension(basename: str) -> str:
    """Lowercased extension for the type check (stored name keeps case)."""
    part = basename.rsplit(".", 1)
    return part[1].lower() if len(part) == 2 else ""


def destination_for_kind(kind: str, basename: str) -> str:
    """Route an upload basename to its normalized media-relative relpath.

    ``adhan`` lands at the media root, ``image`` under ``playlists/``
    (created on write). Anything else — unknown kinds and kind/ext
    mismatches (``image`` + ``.mp3``, ``adhan`` + ``.png``) — is a
    ``ValueError`` the route maps to 422.
    """
    normalized = kind.strip()
    ext = _extension(basename)
    if normalized == "adhan":
        if ext != "mp3":
            raise ValueError(f"kind 'adhan' needs an .mp3 file, got {basename!r}")
        return basename
    if normalized == "image":
        if ext not in _IMAGE_EXTS:
            raise ValueError(
                f"kind 'image' needs one of jpg/jpeg/png/webp, got {basename!r}"
            )
        return f"playlists/{basename}"
    raise ValueError(f"kind must be 'adhan' or 'image': {kind!r}")


def cap_for_kind(kind: str) -> int:
    """Upload byte cap for a validated kind (``kind`` already stripped)."""
    if kind.strip() == "adhan":
        return MAX_AUDIO_BYTES
    return MAX_IMAGE_BYTES


async def read_upload_bounded(upload: UploadFile, cap_bytes: int) -> bytes:
    """Read an upload in chunks, aborting at cap+1 without full buffering.

    A known per-part size over the cap fails before any read; otherwise
    chunks accumulate with a running total and the first chunk past the
    cap raises ``MediaTooLargeError`` (at most cap + one chunk buffered).

    Prefer :func:`read_upload_bounded_sync` in endpoints: this async
    variant blocks the event loop on Pillow/fsync callers anyway, and
    endpoints must be sync ``def`` to hold the shared write lock.
    Retained for the chunked-gate unit test.
    """
    upfront: Any = getattr(upload, "size", None)
    if isinstance(upfront, int) and upfront > cap_bytes:
        raise MediaTooLargeError(cap_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap_bytes:
            raise MediaTooLargeError(cap_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def read_upload_bounded_sync(upload: UploadFile, cap_bytes: int) -> bytes:
    """Sync variant for sync ``def`` endpoints (threadpool, may hold locks).

    Same cap semantics as :func:`read_upload_bounded` but reads the
    already-parsed multipart part via its file object, so callers never
    block the event loop and may hold the shared write lock around the
    final rename. Rewinds first: the framework may have left the cursor
    anywhere.
    """
    upfront: Any = getattr(upload, "size", None)
    if isinstance(upfront, int) and upfront > cap_bytes:
        raise MediaTooLargeError(cap_bytes)
    handle = upload.file
    with suppress(OSError, ValueError):
        handle.seek(0)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap_bytes:
            raise MediaTooLargeError(cap_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def reencode_image(data: bytes, filename: str) -> bytes:
    """Pillow open-verify + re-encode one image (EXIF stripped, format kept).

    The decode enforces Pillow's default ``MAX_IMAGE_PIXELS`` bomb guard
    and the detected format must be one of jpg/png/webp — anything else
    (undecodable bytes, GIF-renamed-to-``.png``) is a ``ValueError``.
    Saving without ``exif``/``info`` drops metadata; JPEG alpha-likes
    convert to RGB so the preserved format stays encodable.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            detected = (img.format or "").upper()
            if detected == "JPG":
                detected = "JPEG"
            if detected not in _IMAGE_FORMATS:
                raise ValueError(f"image file is not a supported image: {filename!r}")
            if detected == "JPEG" and img.mode in ("RGBA", "LA", "PA", "P"):
                converted = img.convert("RGB")
                out = io.BytesIO()
                converted.save(out, format=detected)
                return out.getvalue()
            out = io.BytesIO()
            img.save(out, format=detected)
            return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"image file could not be decoded: {filename!r} ({exc})"
        ) from exc


def verify_audio_mp3(data: bytes, filename: str) -> None:
    """Weak v1 MP3 check: ID3 magic or an MPEG frame-sync word.

    No full decode — content fares beyond the magic are out of scope v1.
    """
    if data[:3] == b"ID3":
        return
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return
    raise ValueError(f"audio file is not a recognized MP3: {filename!r}")


def media_path_for_rel(root: Path, relpath: str) -> Path:
    """Join a normalized relpath under the media root (no escape possible)."""
    return root.joinpath(*relpath.split("/"))


def dest_is_file(dest: Path) -> bool:
    """Pre-write existence probe; overlong names are ``ValueError`` (422).

    The probe itself stats the destination, so ``ENAMETOOLONG`` can
    surface here instead of at rename time — both map the same way.
    """
    try:
        return dest.is_file()
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise ValueError(f"filename too long: {dest.name!r}") from exc
        raise


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    """Write bytes via tmp-in-same-dir + rename (parents mkdir'd first).

    Overlong names surface the OS ``ENAMETOOLONG`` as ``ValueError``
    (the route maps it to 422); every other OS failure propagates for
    the route's 503 mapping.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp-", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, dest)
    except OSError as exc:
        with suppress(OSError):
            os.unlink(tmp_name)
        if exc.errno == errno.ENAMETOOLONG:
            raise ValueError(f"filename too long: {dest.name!r}") from exc
        raise


def list_media_files(root: Path) -> list[dict[str, Any]]:
    """List every file under the media root as [{path, size_bytes}].

    Paths are media-relative posix, sorted for determinism; a missing
    root lists empty (the mount fix mkdirs at boot, but the OpenAPI
    parity app never runs lifespan). Symlinks are skipped (never
    followed): operator/shell-created links could otherwise expose
    outside-root content through the public list, ``/media`` serving,
    and backup export.
    """
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for candidate in sorted(root.rglob("*")):
        try:
            if candidate.is_symlink():
                continue
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
        except OSError:
            # Racing delete/perm change (public list holds no lock):
            # skip rather than 500 the whole listing.
            continue
        entries.append(
            {
                "path": candidate.relative_to(root).as_posix(),
                "size_bytes": size,
            }
        )
    return entries
