"""Parsed-stylesheet effect tests for display presentation knobs (#90).

No browser: asserts the wiring (selectors exist, tokens defined and
consumed), so a knob that stops affecting the cascade fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src" / "muhideen" / "static" / "app.css"

TOKENS = [
    "--green",
    "--green-line",
    "--green-strip",
    "--white",
    "--ink",
    "--glow",
    "--cell-line",
    "--muted-fg",
    "--grad-a",
    "--grad-b",
    "--grad-c",
    "--accent",
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
        assert re.search(re.escape(token) + r"\s*:", block), f":root missing {token}"


def test_palettes_override_every_token() -> None:
    css = CSS.read_text()
    for palette in ("body.palette-midnight", "body.palette-sand"):
        block = _block(css, palette)
        for token in TOKENS:
            assert re.search(re.escape(token) + r"\s*:", block), (
                f"{palette} missing {token}"
            )


def test_rules_consume_tokens() -> None:
    css = CSS.read_text()
    for var in ("--green-line", "--green-strip", "--cell-line", "--muted-fg", "--glow"):
        assert f"var({var})" in css
    assert "--custom-" not in css


def test_font_face_and_font_rules() -> None:
    css = CSS.read_text()
    assert "@font-face" in css
    assert "Outfit-400.woff2" in css
    assert re.search(r"font-display\s*:\s*swap", css)
    _block(css, "body.font-outfit")
    _block(css, "body.font-system")
    # The @font-face URLs are relative to app.css: the files must ship
    # under static/fonts or every display falls back to Arial.
    fonts = CSS.parent / "fonts"
    for weight in ("400", "700", "800"):
        assert (fonts / f"Outfit-{weight}.woff2").is_file()


def test_density_compact_rules() -> None:
    css = CSS.read_text()
    assert "body.density-compact .prayer-row" in css
    assert "body.density-compact .prayer-screen" in css
    assert re.search(r"min-height\s*:", _block(css, "body.density-compact .prayer-row"))
    assert re.search(r"padding\s*:", _block(css, "body.density-compact .prayer-screen"))


def test_density_compact_header_stays_compact() -> None:
    """The body-row minimum must not leak into the timetable header.

    ``body.density-compact .prayer-row`` outranks ``.prayer-row.header``
    at every width (including the 42px mobile rule), so without its own
    header rule compact mode renders a taller header than comfortable.
    """
    css = CSS.read_text()
    header = _block(css, "body.density-compact .prayer-row.header")
    assert re.search(r"min-height\s*:", header)
