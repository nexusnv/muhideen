"""Dim precedence: display pin, else group pin, else default."""

import pytest

from muhideen.core.errors import ConfigError
from muhideen.domain.dim import effective_dim

pytestmark = pytest.mark.unit


def test_default_when_no_pins() -> None:
    assert effective_dim(display_raw=None, group_raw=None, default=20) == (
        20,
        "settings",
    )


def test_display_pin_wins() -> None:
    assert effective_dim(display_raw="30", group_raw=None, default=20) == (
        30,
        "display",
    )


def test_group_pin_applies_without_display_pin() -> None:
    assert effective_dim(display_raw=None, group_raw="30", default=20) == (30, "group")


def test_display_pin_beats_group_pin() -> None:
    assert effective_dim(display_raw="25", group_raw="30", default=20) == (
        25,
        "display",
    )


@pytest.mark.parametrize("raw", ["99", "4", "abc", "", "12.5"])
def test_corrupt_display_pin_raises(raw: str) -> None:
    with pytest.raises(ConfigError):
        effective_dim(display_raw=raw, group_raw=None, default=20)


@pytest.mark.parametrize("raw", ["99", "abc"])
def test_corrupt_group_pin_raises(raw: str) -> None:
    with pytest.raises(ConfigError):
        effective_dim(display_raw=None, group_raw=raw, default=20)


@pytest.mark.parametrize("raw", ["5", "60"])
def test_range_edges_accepted(raw: str) -> None:
    minutes, source = effective_dim(display_raw=raw, group_raw=None, default=20)
    assert (minutes, source) == (int(raw), "display")
