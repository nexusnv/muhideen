"""Adhan audio store: MP3-only byte store with caps (no re-encode)."""

from __future__ import annotations

from pathlib import Path

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
    path.write_bytes(data)
    return path


def delete_adhan_audio(dest_dir: str | Path) -> bool:
    """Remove adhan.mp3; return True when a file was removed."""
    path = Path(dest_dir) / ADHAN_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True
