"""Locale table honesty: every en/ms locale value lives in its owner (pre-flight)."""

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


@pytest.mark.parametrize(
    ("filename", "groups"),
    [
        ("en.json", set(OWNERS)),
        ("ms.json", {"prayer", "boundary"}),
    ],
)
def test_locale_strings_live_in_owners(filename: str, groups: set[str]) -> None:
    table = json.loads((LOCALES / filename).read_text())
    assert set(table) == groups
    for group in groups:
        files = OWNERS[group]
        haystack = "".join((ROOT / f).read_text() for f in files)
        values = [v for v in _walk(table[group]) if "{name}" not in v]
        assert values, f"empty group {group} in {filename}"
        for value in values:
            assert value in haystack, f"{value!r} of [{group}] not found in owners"


def test_ms_key_parity_with_en() -> None:
    en = json.loads((LOCALES / "en.json").read_text())
    ms = json.loads((LOCALES / "ms.json").read_text())
    assert set(ms["prayer"]) == set(en["prayer"])
    assert set(ms["boundary"]) == set(en["boundary"])
