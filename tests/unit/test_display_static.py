"""Static asset guards: CSS legibility rules + palette contrast (1B-1)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / "src" / "muhideen" / "static"

PALETTE = (
    ("#f4f1e8", "#0a3527"),
    ("#e0b73c", "#0a3527"),
    ("#9db8ad", "#0a3527"),
)


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    high, low = max(_luminance(fg), _luminance(bg)), min(_luminance(fg), _luminance(bg))
    return (high + 0.05) / (low + 0.05)


def test_css_legibility_rules_present() -> None:
    import re

    css = (STATIC / "app.css").read_text()
    # Reskin hero: giant countdown digits (10m legibility), card times large.
    assert re.search(r"\.cd-val\s*\{[^}]*font-size:\s*12vh", css)
    assert "3.5vh" in css


def test_palette_contrast_aa() -> None:
    css = (STATIC / "app.css").read_text()
    for fg, bg in PALETTE:
        assert fg in css and bg in css
        assert _contrast(fg, bg) >= 4.5


def test_js_realtime_wiring_present() -> None:
    js = (STATIC / "app.js").read_text()
    for token in (
        "EventSource",
        "/api/events",
        "/api/displays/heartbeat",
        "performance.now",
        "60000",
        "30000",
        "data-state",
        "data-now",
        "data-tzoffset",
        "timeZone",
        "getAttribute",
        "data-countdown",
        "data-bar-start",
        "querySelectorAll",
        "pointerdown",
        "3000",
        "muhideen-dim-skip",
        "localStorage",
    ):
        assert token in js


def test_state_region_selectors_present() -> None:
    css = (STATIC / "app.css").read_text()
    for selector in (
        "#note-pre",
        "#overlay-adhan",
        "#iqamah-hero",
        "#dim",
        "#dim-clock",
        "#dim-skip-hint",
    ):
        assert selector in css


def test_state_template_ids_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    for token in (
        'id="overlay-adhan"',
        'id="iqamah-hero"',
        'id="dim"',
        'id="note-pre"',
        "data-countdown",
        "data-bar-start",
        "hidden data-now",
        "data-dim-until",
    ):
        assert token in html


# Phase 1C reskin tokens (class names locked to the new app.css).
# Mapping note: countdown-box = H/M/S countdown boxes fed by data-countdown
# targets; iqamah-row = per-card iqamah row; brand-block = footer brand block;
# glow-emerald = hero gradient glow. Per-card iqamah id scheme: iqamah-<key>.


def test_js_stage_reload_wiring_present() -> None:
    js = (STATIC / "app.js").read_text()
    for token in (
        "lastStage",
        ".stage",
    ):
        assert token in js


def test_reskin_css_tokens_present() -> None:
    css = (STATIC / "app.css").read_text()
    for token in (
        "countdown-box",
        "iqamah-row",
        "brand-block",
        "glow-emerald",
    ):
        assert token in css


def test_reskin_template_tokens_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    for token in (
        "countdown-box",
        "iqamah-row",
        "brand-block",
        "glow-emerald",
        'id="iqamah-{{ c.key }}"',
        "hijri_long",
        'id="live-clock"',
    ):
        assert token in html


def test_theme_css_variants_present() -> None:
    css = (STATIC / "app.css").read_text()
    for token in (
        "countdown-inline",
        "palette-midnight",
        "palette-sand",
        "font-system",
        "density-compact",
    ):
        assert token in css


def test_theme_template_tokens_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    for token in (
        "countdown-inline",
        "hijri_display",
        "show_boundaries",
        "body_class",
        "data-clock-format",
        "data-dim-minutes",
        "data-dim-source",
    ):
        assert token in html


def test_js_honours_clock_format() -> None:
    js = (STATIC / "app.js").read_text()
    for token in (
        "data-clock-format",
        "clockFmt",
    ):
        assert token in js
