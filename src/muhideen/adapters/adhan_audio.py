"""Adhan audio store: MP3-only byte store with caps (no re-encode)."""

from __future__ import annotations

import os
import posixpath
import re
import tempfile
from pathlib import Path

from muhideen.core.errors import ConfigError

MAX_ADHAN_BYTES = 10 * 1024 * 1024
"""Upload cap: inputs larger than 10MB are rejected before sniffing."""

ADHAN_FILENAME = "adhan.mp3"
"""Canonical stored name: uploads replace each other; the display URL is stable."""


def _is_mp3(data: bytes) -> bool:
    """Sniff MP3 magic: ID3v2 header or MPEG frame sync (no new deps)."""
    if len(data) < 4:
        return False
    if data[:3] == b"ID3":
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def store_adhan_audio(data: bytes, dest_dir: str | Path) -> Path:
    """Validate + store ``data`` as adhan.mp3; return its path.

    Raises ``ValueError`` when over 10MB or not MP3 magic.
    """
    if len(data) > MAX_ADHAN_BYTES:
        raise ValueError(f"adhan audio exceeds 10MB limit: {len(data)} bytes")
    if not _is_mp3(data):
        raise ValueError("unsupported audio format: expected MP3")
    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ADHAN_FILENAME
    # Unique tmp name per upload: concurrent uploads must not share one
    # path, and a failed write must not litter (same-dir replace is atomic).
    fd, tmp_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def delete_adhan_audio(dest_dir: str | Path) -> bool:
    """Remove adhan.mp3; return True when a file was removed."""
    path = Path(dest_dir) / ADHAN_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True


def _strip_legacy_media_prefix(value: str) -> str:
    """Strip the shipped-default ``media/`` prefix to a media-relative rel."""
    if value == "media" or value.startswith("media/"):
        return value[len("media/") :]
    return value


def resolve_adhan_path(file_setting: str, media_dir: str | Path) -> Path:
    """Resolve the configured adhan file strictly inside ``media_dir``.

    Raises :class:`ConfigError` on empty, absolute, or escaping values.
    """
    if not file_setting:
        raise ConfigError("adhan_audio.file must be non-empty")
    candidate = file_setting.replace("\\", "/")
    if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        raise ConfigError(f"adhan_audio.file must be relative: {file_setting!r}")
    rel = _strip_legacy_media_prefix(candidate)
    norm = posixpath.normpath(rel)
    if norm == ".." or norm.startswith("../"):
        raise ConfigError(f"adhan_audio.file escapes the media root: {file_setting!r}")
    root = Path(media_dir).resolve()
    path = (root / Path(norm)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ConfigError(
            f"adhan_audio.file escapes the media root: {file_setting!r}"
        ) from None
    return path


def adhan_url_for(file_setting: str, media_dir: str | Path) -> str:
    """Stable public URL for the resolved adhan file (no hashes)."""
    del media_dir  # reserved: static-root deployments serve under /media too
    rel = _strip_legacy_media_prefix(file_setting.replace("\\", "/"))
    return f"/media/{posixpath.normpath(rel)}"
