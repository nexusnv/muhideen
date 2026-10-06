"""Parsed-stylesheet effect tests for display presentation knobs (#90).

No browser: asserts the wiring (selectors exist, tokens defined and
consumed), so a knob that stops affecting the cascade fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src" / "muhideen" / "static" / "app.css"

TOKENS = [
    "--green", "--green-line", "--green-strip", "--white", "--ink",
    "--glow", "--cell-line", "--muted-fg",
    "--grad-a", "--grad-b", "--grad-c", "--accent",
]


def _block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert match, f"missing rule for {selector}"
    return match.group(1)


def test_root_defines_classic_tokens() -> None:
    block = _block(CSS.read_text(), ":root")
    assert "--green:#075743" in block.replace(" ", "")
    assert "--white:#f8fbf9" in block.replace(" ", "")
    for token in TOKENS:
        assert token in block


def test_palettes_override_every_token() -> None:
    css = CSS.read_text()
    for palette in ("body.palette-midnight", "body.palette-sand"):
        block = _block(css, palette)
        for token in TOKENS:
            assert token in block, f"{palette} missing {token}"


def test_rules_consume_tokens_not_hardcoded_hex() -> None:
    css = CSS.read_text()
    for var in ("--green-line", "--green-strip", "--cell-line", "--muted-fg", "--glow"):
        assert f"var({var})" in css
    assert "--custom-" not in css


def test_font_face_and_font_rules() -> None:
    css = CSS.read_text()
    assert "@font-face" in css
    assert "Outfit-400.woff2" in css
    assert "font-display: swap" in css.replace("font-display:swap", "font-display: swap")
    _block(css, "body.font-outfit")
    _block(css, "body.font-system")


def test_density_compact_rules() -> None:
    css = CSS.read_text()
    assert "body.density-compact .prayer-row" in css
    assert "body.density-compact .prayer-screen" in css
