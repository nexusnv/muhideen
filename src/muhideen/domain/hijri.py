"""Hijri offset: bounded shift of an already-computed Hijri date."""

from __future__ import annotations

from datetime import date, timedelta

from muhideen.core.errors import ConfigError


def apply_hijri_offset(base_hijri: date, offset_days: int) -> date:
    """Shift a Hijri date by the configured regional offset (-2..2)."""
    if not -2 <= offset_days <= 2:
        raise ConfigError(f"hijri_offset out of range: {offset_days}")
    return base_hijri + timedelta(days=offset_days)
