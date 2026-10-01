"""Admin static guards: tokens, DEFAULTS mirror, bilingual hooks (1B-3)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / "src" / "muhideen" / "static"


def test_admin_js_wiring_present() -> None:
    js = (STATIC / "admin.js").read_text()
    for token in (
        "/api/auth/login",
        "/api/auth/setup",
        "/api/settings",
        "/api/playlists",
        "/api/displays",
        "429",
        "imsak_offset_min",
        "dhuha_offset_min",
        "countdown_before_adhan_min",
        "countdown_before_adhan_overrides",
        "s-countdown-default",
        "setupDone",
        "disabled",
        "on Stage now",
        "next at",
        "dim_minutes_override",
        "duration_s",
        "Wrong password",
        "Too many attempts, wait a minute",
        "Network error",
    ):
        assert token in js
    for absent in ("setLang", "data-en", "var STR", "lang-toggle"):
        assert absent not in js
    # Per-display Theme input is dead (render reads settings theme only):
    # the admin sends group_name alone and keeps no theme read.
    for absent in ("data-theme-for", "current_theme"):
        assert absent not in js


def test_admin_js_defaults_cover_settings_dto() -> None:
    import re

    from muhideen.api.dto import SettingsDTO

    js = (STATIC / "admin.js").read_text()
    block = js.split("var DEFAULTS = {", 1)[1].split("\n};", 1)[0]
    keys = set(re.findall(r'"([a-z_]+)":', block))
    assert set(SettingsDTO.model_fields) <= keys


def test_admin_js_defaults_match_theme_seam() -> None:
    import re

    from muhideen.core.values import THEME_DEFAULTS

    js = (STATIC / "admin.js").read_text()
    theme_block = js.split('"theme": {', 1)[1].split("}", 1)[0]
    pairs = dict(re.findall(r'"([a-z_]+)":\s*"([^"]+)"', theme_block))
    assert pairs == THEME_DEFAULTS


def test_admin_js_bodies_share_defaults() -> None:
    js = (STATIC / "admin.js").read_text()
    assert "function settingsBodyFromDefaults(overrides)" in js
    assert js.count("settingsBodyFromDefaults(") >= 2
    assert js.count("JSON.parse(JSON.stringify(DEFAULTS))") == 1


def test_admin_css_touch_targets() -> None:
    css = (STATIC / "admin.css").read_text()
    assert "min-height: 48px" in css
    assert "max-width: 1120px" in css
    assert "max(1rem, 16px)" in css
    for token in (
        "admin-nav",
        "admin-section",
        ".btn",
        "playlist-editor",
        "occupancy-preview",
        "badge",
        "sidebar",
        "glance-grid",
        "setting-row",
        "switch",
        "matrix-table",
    ):
        assert token in css


def test_admin_css_has_no_remote_refs() -> None:
    css = (STATIC / "admin.css").read_text()
    assert "http" not in css
    assert "@import" not in css


def test_admin_templates_english_only() -> None:
    views = STATIC.parent / "views" / "templates" / "admin"
    for name in ("login.html", "setup.html", "settings.html", "playlists.html"):
        html = (views / name).read_text()
        assert "data-en" not in html
        assert "lang-toggle" not in html
        assert 'lang="en"' in html
    login = (views / "login.html").read_text()
    assert "Password" in login
    assert "Login" in login
    settings = (views / "settings.html").read_text()
    assert "Save" in settings
    assert "Settings" in settings


def test_admin_templates_share_grouped_nav() -> None:
    views = STATIC.parent / "views" / "templates" / "admin"
    nav = (views / "_nav.html").read_text()
    for label in ("Profile", "Time", "Display", "Playlists", "System"):
        assert label in nav
    assert "/admin/playlists" in nav
    settings = (views / "settings.html").read_text()
    assert "admin/_nav.html" in settings
    playlists = (views / "playlists.html").read_text()
    assert "admin/_nav.html" in playlists
    assert "playlist-editor" in playlists
    assert "occupancy-preview" in playlists


def test_admin_theme_knob_wiring_present() -> None:
    js = (STATIC / "admin.js").read_text()
    for token in (
        "s-theme-palette",
        "s-theme-font",
        "s-theme-countdown",
        "s-theme-clock",
        "s-theme-hijri",
        "s-theme-boundary",
        "s-theme-density",
    ):
        assert token in js
    settings = (
        STATIC.parent / "views" / "templates" / "admin" / "settings.html"
    ).read_text()
    for token in (
        'id="s-theme-palette"',
        'id="s-theme-font"',
        'id="s-theme-countdown"',
        'id="s-theme-clock"',
        'id="s-theme-hijri"',
        'id="s-theme-boundary"',
        'id="s-theme-density"',
        "current.theme.palette",
    ):
        assert token in settings
