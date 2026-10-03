"""Static asset guards: CSS legibility rules + palette contrast (1B-1)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / "src" / "muhideen" / "static"

PALETTE = (
    ("#f8fbf9", "#075743"),
    ("#e0b73c", "#0a3527"),
)


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    high, low = max(_luminance(fg), _luminance(bg)), min(_luminance(fg), _luminance(bg))
    return (high + 0.05) / (low + 0.05)


def test_css_legibility_rules_present() -> None:
    css = (STATIC / "app.css").read_text()
    # Mockup screen: prayer rows highlight the next prayer, the clock colon
    # blinks (seconds tick without digits), countdown uses tabular figures.
    assert ".prayer-row.current" in css
    assert "colon-blink" in css
    assert "tabular-nums" in css
    assert "clamp(" in css


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
        "/api/next-event",
        "performance.now",
        "60000",
        "data-state",
        "data-now",
        "data-tzoffset",
        "timeZone",
        "getAttribute",
        "data-target",
        "data-dim-until",
        "querySelectorAll",
        "clock-h",
        "clock-m",
        "adhan-audio",
        "data-volume",
        "pointerdown",
        "3000",
        "muhideen-dim-skip",
        "localStorage",
    ):
        assert token in js


def test_js_uses_text_content_only() -> None:
    js = (STATIC / "app.js").read_text()
    assert "innerHTML" not in js


def test_state_region_selectors_present() -> None:
    css = (STATIC / "app.css").read_text()
    for selector in (
        "#hero-clock[hidden]",
        "#banners",
        ".prayer-row.current",
        ".colon",
        "colon-blink",
        "state-salah_dim",
        "#dim-skip[hidden]",
        "#adhan-audio",
        ".countdown",
        ".slate-error",
    ):
        assert selector in css


def test_state_template_ids_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    for token in (
        'id="hero-clock"',
        'id="row-{{ c.key }}"',
        'id="iqamah-{{ c.key }}"',
        'id="bounds"',
        'id="bound-{{ b.key }}"',
        'id="live-clock"',
        'id="countdown-label"',
        'id="countdown"',
        "data-target",
        "hidden data-now",
        "data-dim-until",
        'id="banners"',
        'id="adhan-audio"',
        'id="dim-skip"',
    ):
        assert token in html


# Mockup-screen tokens (class names locked to the new app.css/template).
# Mapping note: prayer-screen = two-column grid; prayer-row = timetable row
# (.current = highlighted next prayer); additional-times = bounds strip;
# clock-block/countdown-block/mosque-block = right-column blocks.


def test_js_stage_reload_wiring_present() -> None:
    js = (STATIC / "app.js").read_text()
    for token in (
        "lastStage",
        ".stage",
    ):
        assert token in js


def test_mockup_css_tokens_present() -> None:
    css = (STATIC / "app.css").read_text()
    for token in (
        "prayer-screen",
        "prayer-row",
        "additional-times",
        "clock-block",
        "countdown-block",
        "mosque-block",
    ):
        assert token in css


def test_mockup_template_tokens_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    for token in (
        "prayer-screen",
        "prayer-row",
        "additional-times",
        "clock-block",
        "countdown-block",
        "mosque-block",
        'id="iqamah-{{ c.key }}"',
        "gregorian_long",
        "hijri_display",
        'id="live-clock"',
    ):
        assert token in html


def test_theme_template_tokens_present() -> None:
    html = (STATIC.parent / "views" / "templates" / "display.html").read_text()
    # No data-clock-format / data-dim-minutes / data-dim-source / countdown-inline:
    # the screen is design-locked 12h and ignores the knobs by design (they
    # stay settable for future surfaces; dim surfaces only via data-dim-until).
    for token in (
        "hijri_display",
        "gregorian_long",
        "show_boundaries",
        "body_class",
        "countdown_label",
        "countdown_target",
        "clock_hm",
        "clock_period",
        "dim_until_iso",
        "adhan_audio_url",
        "banners",
    ):
        assert token in html
    for token in (
        "data-clock-format",
        "data-dim-minutes",
        "data-dim-source",
        "countdown-inline",
    ):
        assert token not in html


def test_js_honours_clock_format() -> None:
    # Design-locked 12h: the screen ignores the theme knob, so JS resolves
    # wall time from the server epoch plus the tz attrs (no clock-format
    # branching).
    js = (STATIC / "app.js").read_text()
    for token in (
        "data-tz",
        "timeZone",
        "anchor",
        "serverEpoch",
    ):
        assert token in js
