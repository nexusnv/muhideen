"""Computed-style checks for palette-only presentation wiring (#90).

Loads the real display HTML with the real stylesheet: the /static link is
rewritten to a file:// URL so headless Chromium applies the true cascade
(no test server, no ports). Skips when Playwright/Chromium is unavailable
so unit/e2e stays green on machines without browsers.

Colors come only from the selected palette: each test sets
``theme.palette`` (global default or per-display overlay) and asserts the
computed cascade. There is no per-color override to test.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

EXAMPLE = (
    Path(__file__).resolve().parent.parent.parent / "config" / "muhideen.example.json"
)
APP_CSS = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "muhideen"
    / "static"
    / "app.css"
)
KL = timezone(timedelta(hours=8))
PINNED = datetime(2025, 10, 20, 12, 20, tzinfo=KL)


@pytest.fixture
def browser():
    """Fresh Chromium per test; skips (not fails) without a browser binary.

    Function scope by design: the browser closes before the test ends so
    no Playwright driver thread outlives the test (a session-scoped
    browser left open breaks the later live-TCP SSE tests in
    tests/e2e/test_events_stream.py).
    """
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as host:
        try:
            launched = host.chromium.launch()
        except Exception as exc:  # missing binary / sandbox / libs
            # CI installs Chromium up front, so a launch failure there
            # means the cascade goes unchecked behind a green skip:
            # fail instead. Local runs without a browser still skip.
            if os.environ.get("CI") == "true" or "GITHUB_ACTIONS" in os.environ:
                raise
            pytest.skip(f"Chromium unavailable: {exc}")
        yield launched
        launched.close()


def _rendered_html(
    tmp_path: Path,
    display_id: str,
    palette: str | None = None,
) -> str:
    from fastapi.testclient import TestClient

    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.app import AppDeps, create_app

    class _Clock:
        def __init__(self, now: datetime) -> None:
            self._now = now

        def now(self) -> datetime:
            return self._now

    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    # Palette-only selection: override the target display's theme palette
    # when asked; None keeps the example file untouched so the bare
    # palette rule applies.
    if palette is not None:
        raw["displays"][display_id]["theme"]["palette"] = palette
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    cfg = load_config_file(dest)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=_Clock(PINNED),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
    )
    with TestClient(create_app(deps)) as client:
        html = client.get("/display", params={"id": display_id}).text
    return html.replace("/static/app.css", APP_CSS.as_uri())


def _computed(
    browser,
    tmp_path: Path,
    display_id: str,
    script: str,
    palette: str | None = None,
) -> str:
    html = _rendered_html(tmp_path, display_id, palette)
    # set_content() serves from about:blank, from which Chromium
    # blocks file:// subresources ("Not allowed to load local
    # resource"), leaving the cascade empty. Navigating to a
    # file:// document instead lets the file:// stylesheet load:
    # same real HTML, same real stylesheet, no test server.
    doc = tmp_path / f"{display_id}.html"
    doc.write_text(html)
    page_handle = browser.new_page()
    try:
        page_handle.goto(doc.as_uri())
        return str(page_handle.evaluate(script))
    finally:
        page_handle.close()


def test_midnight_palette_renders_dark(browser, tmp_path: Path) -> None:
    assert (
        _computed(
            browser,
            tmp_path,
            "entrance",
            "getComputedStyle(document.body).backgroundColor",
        )
        == "rgb(11, 15, 14)"
    )


def test_sand_palette_overlay_renders_light(browser, tmp_path: Path) -> None:
    assert (
        _computed(
            browser,
            tmp_path,
            "entrance",
            "getComputedStyle(document.body).backgroundColor",
            palette="sand",
        )
        == "rgb(236, 228, 207)"
    )


def test_palette_gradient_follows_visible_screen(browser, tmp_path: Path) -> None:
    """The visible screen gradient comes from the selected palette.

    `.prayer-screen` covers `100vh` with its own gradient tokens, so the
    assertion reads the screen element itself, not just `<body>` behind it.
    """
    screen_bg = _computed(
        browser,
        tmp_path,
        "entrance",
        "getComputedStyle(document.querySelector('.prayer-screen')).backgroundImage",
        palette="sand",
    )
    assert "236, 228, 207" in screen_bg


def test_classic_palette_keeps_green_background(browser, tmp_path: Path) -> None:
    assert (
        _computed(
            browser,
            tmp_path,
            "main-hall",
            "getComputedStyle(document.body).backgroundColor",
        )
        == "rgb(7, 87, 67)"
    )
