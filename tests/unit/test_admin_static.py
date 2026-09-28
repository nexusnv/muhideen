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
        "current_theme",
        "dim_minutes_override",
        "duration_s",
        "Wrong password",
        "Too many attempts, wait a minute",
        "Network error",
    ):
        assert token in js
    for absent in ("setLang", "data-en", "var STR", "lang-toggle"):
        assert absent not in js


def test_admin_js_defaults_cover_settings_dto() -> None:
    import re

    from muhideen.api.dto import SettingsDTO

    js = (STATIC / "admin.js").read_text()
    block = js.split("var DEFAULTS = {", 1)[1].split("\n};", 1)[0]
    keys = set(re.findall(r'"([a-z_]+)":', block))
    assert set(SettingsDTO.model_fields) <= keys


def test_admin_css_touch_targets() -> None:
    css = (STATIC / "admin.css").read_text()
    assert "min-height: 48px" in css
    assert "max-width: 960px" in css
    assert "max(2.2vh, 16px)" in css
    for token in (
        "admin-nav",
        "form-grid",
        "admin-section",
        ".btn",
        "playlist-editor",
        "occupancy-preview",
        "badge",
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
