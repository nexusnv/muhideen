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
    assert re.search(r"#hero-clock\s*\{[^}]*font-size:\s*12vh", css)
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
        "timeZone",
        "getAttribute",
    ):
        assert token in js
