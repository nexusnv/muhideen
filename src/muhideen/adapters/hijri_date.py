"""Hijri date resolution over hijridate (Umm al-Qura, FR-1.5 display use).

Pure date math: apply the Gregorian day offset, then convert.
Absolute-day equivalence makes a Gregorian shift exactly equal to a
Hijri-day shift, so no Hijri month arithmetic is needed. Dates outside
the library's 1343–1500 AH range yield None, never raise.
"""

from __future__ import annotations

from datetime import date, timedelta

from hijridate import Gregorian


def resolve_hijri(day: date, offset: int) -> str | None:
    """Return ``YYYY-MM-DD`` Hijri for ``day + offset`` days, else None."""
    try:
        shifted = day + timedelta(days=offset)
        hijri = Gregorian(shifted.year, shifted.month, shifted.day).to_hijri()
    except (ValueError, OverflowError):
        return None
    return f"{hijri.year:04d}-{hijri.month:02d}-{hijri.day:02d}"
