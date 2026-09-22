"""Skeleton: package imports cleanly (tripwire, not slice logic)."""

import pytest

import muhideen


@pytest.mark.unit
def test_package_imports() -> None:
    assert muhideen.__name__ == "muhideen"


@pytest.mark.unit
def test_package_ships_py_typed() -> None:
    from pathlib import Path

    assert (Path(muhideen.__file__).parent / "py.typed").is_file()
