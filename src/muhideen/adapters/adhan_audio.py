"""Adhan file: configured media-relative path resolved inside the media dir."""

from __future__ import annotations

from pathlib import Path

from muhideen.core.errors import ConfigError
from muhideen.core.values import normalize_adhan_rel, strip_legacy_media_prefix

ADHAN_FILENAME = "adhan.mp3"
"""Default adhan file: the display URL is stable unless the config changes it."""

__all__ = [
    "ADHAN_FILENAME",
    "normalize_adhan_rel",
    "resolve_adhan_path",
    "strip_legacy_media_prefix",
]


def resolve_adhan_path(file_setting: str, media_dir: str | Path) -> Path:
    """Resolve the configured adhan file strictly inside ``media_dir``.

    Raises :class:`ConfigError` on empty, absolute, URL-structural,
    or escaping values (via :func:`normalize_adhan_rel`), including
    symlink escapes detected after resolution. A symlink loop makes
    :meth:`Path.resolve` raise :class:`RuntimeError` (py3.11/3.12);
    that maps here too so callers keep serving the setup slate.
    """
    try:
        norm = normalize_adhan_rel(file_setting)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        root = Path(media_dir).resolve()
        path = (root / Path(norm)).resolve()
    except RuntimeError as exc:
        raise ConfigError(
            f"adhan_audio.file cannot be resolved: {file_setting!r} ({exc})"
        ) from exc
    try:
        path.relative_to(root)
    except ValueError:
        raise ConfigError(
            f"adhan_audio.file escapes the media root: {file_setting!r}"
        ) from None
    return path
