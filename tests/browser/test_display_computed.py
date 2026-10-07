"""Computed-style checks for presentation wiring (#90).

Loads the real display HTML with the real stylesheet: the /static link is
rewritten to a file:// URL so headless Chromium applies the true cascade
(no test server, no ports). Skips when Playwright/Chromium is unavailable
so unit/e2e stays green on machines without browsers.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

playwright = pytest.importorskip("playwright.sync_api")

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


def _rendered_html(
    tmp_path: Path, display_id: str, custom_bg: str | None = "#123456"
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
    # Divergent custom color (differs from the midnight palette) so the
    # computed-style assertions prove operator overrides win (a value
    # duplicating the palette would mask shadowing). None drops the
    # custom block to prove the bare palette rule applies.
    if custom_bg is None:
        del raw["displays"]["entrance"]["custom_colors"]
    else:
        raw["displays"]["entrance"]["custom_colors"]["background"] = custom_bg
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


def _background_rgb(
    page: object, tmp_path: Path, display_id: str, custom_bg: str | None = "#123456"
) -> str:
    from playwright.sync_api import sync_playwright

    html = _rendered_html(tmp_path, display_id, custom_bg)
    with sync_playwright() as browser_host:
        browser = browser_host.chromium.launch()
        try:
            page_handle = browser.new_page()
            # set_content() serves from about:blank, from which Chromium
            # blocks file:// subresources ("Not allowed to load local
            # resource"), leaving the cascade empty. Navigating to a
            # file:// document instead lets the file:// stylesheet load:
            # same real HTML, same real stylesheet, no test server.
            doc = tmp_path / f"{display_id}.html"
            doc.write_text(html)
            page_handle.goto(doc.as_uri())
            return str(
                page_handle.evaluate("getComputedStyle(document.body).backgroundColor")
            )
        finally:
            browser.close()


def test_custom_background_overrides_palette(tmp_path: Path) -> None:
    assert _background_rgb(None, tmp_path, "entrance") == "rgb(18, 52, 86)"


def test_midnight_palette_without_custom_renders_dark(tmp_path: Path) -> None:
    assert _background_rgb(None, tmp_path, "entrance", None) == "rgb(11, 15, 14)"


def test_classic_palette_keeps_green_background(tmp_path: Path) -> None:
    assert _background_rgb(None, tmp_path, "main-hall") == "rgb(7, 87, 67)"
