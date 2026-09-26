"""Hijri date resolution guards (slice 1B-1 backend pre-step)."""

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def test_golden_vector_2025_10_20() -> None:
    from muhideen.adapters.hijri_date import resolve_hijri

    assert resolve_hijri(date(2025, 10, 20), 0) == "1447-04-28"


def test_offset_shifts_gregorian_day() -> None:
    from muhideen.adapters.hijri_date import resolve_hijri

    assert resolve_hijri(date(2025, 10, 20), 1) == resolve_hijri(date(2025, 10, 21), 0)
    assert resolve_hijri(date(2025, 10, 20), -2) == resolve_hijri(date(2025, 10, 18), 0)


def test_out_of_library_range_returns_none() -> None:
    from muhideen.adapters.hijri_date import resolve_hijri

    assert resolve_hijri(date(1900, 1, 1), 0) is None
