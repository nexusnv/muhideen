"""Hijri offset guards (slice 1A-2, Task 3)."""

from datetime import date

import pytest

from muhideen.core.errors import ConfigError


@pytest.mark.unit
def test_offset_zero_identity() -> None:
    from muhideen.domain.hijri import apply_hijri_offset

    assert apply_hijri_offset(date(1447, 3, 29), 0) == date(1447, 3, 29)


@pytest.mark.unit
def test_offset_plus_two() -> None:
    from muhideen.domain.hijri import apply_hijri_offset

    assert apply_hijri_offset(date(1447, 3, 29), 2) == date(1447, 3, 31)


@pytest.mark.unit
def test_offset_minus_two() -> None:
    from muhideen.domain.hijri import apply_hijri_offset

    assert apply_hijri_offset(date(1447, 3, 29), -2) == date(1447, 3, 27)


@pytest.mark.unit
def test_offset_out_of_range_raises() -> None:
    from muhideen.domain.hijri import apply_hijri_offset

    with pytest.raises(ConfigError):
        apply_hijri_offset(date(1447, 3, 29), 3)
    with pytest.raises(ConfigError):
        apply_hijri_offset(date(1447, 3, 29), -3)
