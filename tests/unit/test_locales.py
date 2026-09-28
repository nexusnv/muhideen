"""Locale table honesty: every en.json value lives in its owner (pre-flight)."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "locales"

OWNERS = {
    "prayer": ["src/muhideen/views/display.py"],
    "boundary": ["src/muhideen/views/display.py"],
    "display": [
        "src/muhideen/views/display.py",
        "src/muhideen/views/templates/display.html",
    ],
    "admin_login": [
        "src/muhideen/views/templates/admin/login.html",
        "src/muhideen/static/admin.js",
    ],
    "admin_setup": [
        "src/muhideen/views/templates/admin/setup.html",
        "src/muhideen/static/admin.js",
    ],
    "admin_settings": [
        "src/muhideen/views/templates/admin/settings.html",
        "src/muhideen/static/admin.js",
    ],
}


def _walk(node: object) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_walk(value))
        return out
    assert isinstance(node, str)
    return [node]


def test_every_english_string_lives_in_its_owner() -> None:
    table = json.loads((LOCALES / "en.json").read_text())
    assert set(table) == set(OWNERS)
    for group, files in OWNERS.items():
        haystack = "".join((ROOT / f).read_text() for f in files)
        values = [v for v in _walk(table[group]) if "{name}" not in v]
        assert values, f"empty group {group}"
        for value in values:
            assert value in haystack, f"{value!r} of [{group}] not found in owners"
