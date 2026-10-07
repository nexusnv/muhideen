# Display Presentation Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the display presentation knobs (`palette`, `font`, `density`, `custom_colors`) to the stylesheet so configured themes actually render, and strengthen the BM label test to per-row assertions (issues #90 + #61).

**Architecture:** Variable-token design in `app.css`: `:root` holds the classic-green tokens byte-identical, `body.palette-*` blocks override the same tokens, `body.font-*`/`body.density-*` add the remaining overlays, and the template's `<style>` block rebinds surface tokens for `custom_colors`. No Python model or context changes (emission names change in the template only). Tests assert stylesheet effect (parsed CSS), template emission/placement, and one Playwright computed-style check.

**Tech Stack:** Jinja2 template, hand-written CSS with custom properties, pytest (unit/e2e), Playwright Chromium (new `browser` marker).

---

## File map

- Modify: `src/muhideen/static/app.css` — tokenize hardcoded colors, add palette/font/density rules, `@font-face`.
- Modify: `src/muhideen/views/templates/display.html:7` — `custom_colors` rebinds surface tokens.
- Modify: `tests/test_display_from_files.py` — row-scoped BM assertions (#61), custom rebind assertions.
- Create: `tests/test_display_stylesheet.py` — parsed-CSS effect tests (no browser).
- Create: `tests/browser/test_display_computed.py` — Playwright computed-style test.
- Create: `tests/browser/__init__.py` — empty package marker.
- Modify: `pyproject.toml` — `playwright>=1.49` in `[dependency-groups] dev`, `browser` pytest marker.
- Modify: `.github/workflows/ci.yml` — Chromium install step.
- Modify: `TESTING_STRATEGY.md` — `tests/browser/` row.
- Modify: `CHANGELOG.md` — `[Unreleased] Added` entry.

Deliberately untouched: `src/muhideen/core/values.py` (`theme_css_class` output already correct; unit pins at `tests/unit/test_display_context.py:210-240` keep passing), `src/muhideen/views/display.py` (context keys already emitted), `config/muhideen.example.json` (valid examples already exercise midnight + custom colors). `countdown_style` stays accepted-but-unified per the design-locked single countdown format (redesign plan: "always `HH:MM:SS` (no boxes/labels)") — pinned by test, no visual fork.

---

## Knob → consumer table (verified this session)

| Knob | Values | Current consumer | Plan action |
|---|---|---|---|
| `palette` | classic-green, midnight, sand | `body_class` only, no CSS rules | Add `body.palette-*` token overrides |
| `font` | outfit, system | `body_class` only; body hardcodes Arial; Outfit woff2 ships unused | `@font-face` + `body.font-*` rules; default body keeps Arial stack, outfit overrides (default theme font is outfit, so default rendering changes Arial→Outfit intentionally) |
| `density` | comfortable, compact | `body_class` only | Add `body.density-compact` reductions |
| `custom_colors` | background/foreground/accent | `<style>:root{--custom-*}` emitted, zero consumers | Rebind surface tokens instead (table below) |
| `countdown_style` | boxes, inline | `countdown_inline` context key, consumed nowhere | Pin unified format with test; no fork |
| `clock_format`, `hijri_form`, `boundary_strip` | — | Consumed (`clock`, `hijri_display`, `show_boundaries`) | No change |

Custom rebind mapping (template emits only keys present in the dict). The
rebind targets `body` with `!important`: a `:root` rebind would be
silently shadowed under midnight/sand, because `body.palette-*` sets the
same tokens on `<body>` and descendants inherit from `body`, not `<html>`
— same-element order only helps the classic path. `!important` here is
operator-preference-over-theme, the one legitimate use:

| custom key | Emitted declaration (`<style>body{… !important}</style>`) | Overrides |
|---|---|---|
| `background` | `--green:<v> !important;` | page surfaces, gradient base, strips |
| `foreground` | `--white:<v> !important;` | body text, logo, header muted |
| `accent` | `--accent:<v> !important;` | current-row inset ring (new chrome, see Task 3) |

Token table (`:root` = classic-green, byte-identical to current values):

```
--green:#075743; --green-line:#16846e; --green-strip:rgba(0,49,38,.42);
--white:#f8fbf9; --ink:#075743;
--glow:rgba(12,112,85,.34); --cell-line:rgba(22,132,110,.78);
--muted-fg:rgba(248,251,249,.82);
--grad-a:#075440; --grad-b:#075743; --grad-c:#075541;
--accent:transparent;
```

Palette overrides (proposal — Task 1 verifies against the approved mockup; structure fixed, hex values adjustable):

```css
body.palette-midnight {
  --green:#0b0f0e; --green-line:#2e3a36; --green-strip:rgba(0,0,0,.45);
  --white:#f2f2f2; --ink:#0b0f0e;
  --glow:rgba(0,0,0,.5); --cell-line:rgba(120,135,130,.5);
  --muted-fg:rgba(242,242,242,.75);
  --grad-a:#0b0f0e; --grad-b:#101615; --grad-c:#0b0f0e;
  --accent:#c9a227;
}
body.palette-sand {
  --green:#ece4cf; --green-line:#c2b694; --green-strip:rgba(120,100,60,.16);
  --white:#33302a; --ink:#33302a;
  --glow:rgba(150,130,80,.25); --cell-line:rgba(120,110,85,.55);
  --muted-fg:rgba(51,48,42,.72);
  --grad-a:#e7dcc2; --grad-b:#ece4cf; --grad-c:#e3d7ba;
  --accent:#8a6d1f;
}
```

---

### Task 1: Phase 0 re-analysis (no code changes)

**Files:** read-only: `src/muhideen/views/templates/display.html:7`, `src/muhideen/static/app.css:1-7`, `docs/development/plans/2026-10-03-display-redesign.md:95`, `docs/development/plans/2026-10-01-display-bm-labels.md:35`

- [ ] **Step 1: Confirm the `:root` syntax fix already landed**

Run: `sed -n '7p' src/muhideen/views/templates/display.html`
Phase 0 outcome (recorded 2026-10-07): braces are ABSENT — the `{` after `:root` opens the Jinja `{% for %}` tag, and rendering produces `<style>:root--custom-accent:red;</style>` (invalid CSS, exactly as #90 states). The syntax fix is therefore required work and is folded into Task 4's rebind (which emits literal braces via `{{ '{' }}` / `{{ '}' }}`). Task 4's commit message must record that the braceless form was still present at Phase 0.

- [ ] **Step 2: Confirm the knob→consumer table above**

Run: `grep -n "palette-\|font-\|density-\|countdown-" src/muhideen/static/app.css; grep -rn "countdown_inline" src/muhideen --include='*.py' --include='*.html' --include='*.js'`
Expected: no `palette-*`/`font-*`/`density-*` selectors; `countdown_inline` produced only in `views/display.py`. This confirms overlay wiring is the whole gap.

- [ ] **Step 3: Confirm the #61 design constraint**

Read `docs/development/plans/2026-10-03-display-redesign.md` around line 95: the redesign intentionally renders only the primary prayer name ("template no longer renders them [bm/ar]"). Therefore triple-adjacency rendering is out of scope by design; Task 8 asserts the primary label inside its own row element (Option A). Do not render secondary labels.

- [ ] **Step 4: Verify palette hexes against the approved mockup**

Compare the midnight/sand tables above with the approved mockup. Adjust hex values only — selector names, token names, and rule structure are locked by this plan. If no mockup is reachable, proceed with the values as written.

No commit (research only).

---

### Task 2: Stylesheet effect tests (failing first)

**Files:**
- Create: `tests/test_display_stylesheet.py`
- Test: `tests/test_display_stylesheet.py`

- [ ] **Step 1: Write the failing tests**

```python
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
        assert token in block


def test_palettes_override_every_token() -> None:
    css = CSS.read_text()
    for palette in ("body.palette-midnight", "body.palette-sand"):
        block = _block(css, palette)
        for token in TOKENS:
            assert token in block, f"{palette} missing {token}"


def test_rules_consume_tokens() -> (
    None
):  # renamed from test_rules_consume_tokens_not_hardcoded_hex during review (name must not promise a hex scan); token loops use declaration-boundary matching `re.search(re.escape(token) + r"\s*:", block)`
    css = CSS.read_text()
    for var in ("--green-line", "--green-strip", "--cell-line", "--muted-fg", "--glow"):
        assert f"var({var})" in css
    assert "--custom-" not in css


def test_font_face_and_font_rules() -> None:
    css = CSS.read_text()
    assert "@font-face" in css
    assert "Outfit-400.woff2" in css
    assert "font-display: swap" in css.replace(
        "font-display:swap", "font-display: swap"
    )
    _block(css, "body.font-outfit")
    _block(css, "body.font-system")


def test_density_compact_rules() -> None:
    css = CSS.read_text()
    assert "body.density-compact .prayer-row" in css
    assert "body.density-compact .prayer-screen" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_display_stylesheet.py -q`
Expected: FAIL (missing rules/selectors — 5 failed).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_display_stylesheet.py
git commit -m "test(display): stylesheet effect tests for presentation knobs (#90)"
```

---

### Task 3: Tokenize `app.css` + palette/font/density rules

**Files:**
- Modify: `src/muhideen/static/app.css:1-23,54-56,73,91,117-123,131,141,172-173,224,249`
- Test: `tests/test_display_stylesheet.py`

- [ ] **Step 1: Extend `:root` with the token table (classic values)**

Replace `src/muhideen/static/app.css:1-7` with:

```css
:root {
  --green: #075743;
  --green-line: #16846e;
  --green-strip: rgba(0, 49, 38, .42);
  --white: #f8fbf9;
  --ink: #075743;
  --glow: rgba(12, 112, 85, .34);
  --cell-line: rgba(22, 132, 110, .78);
  --muted-fg: rgba(248, 251, 249, .82);
  --grad-a: #075440;
  --grad-b: #075743;
  --grad-c: #075541;
  --accent: transparent;
}
```

- [ ] **Step 2: Point hardcoded rules at tokens (classic rendering byte-identical)**

Apply exactly these replacements (nothing else in the file moves):

```css
/* body rule: */ background: var(--green); color: var(--white);
/* .prayer-screen background: */
  background:
    radial-gradient(ellipse at 25% 35%, var(--glow), transparent 55%),
    linear-gradient(110deg, var(--grad-a) 0%, var(--grad-b) 52%, var(--grad-c) 100%);
/* .prayer-row border-bottom: */ border-bottom: 1px solid var(--green-line);
/* .cell border-right: */ border-right: 1px solid var(--cell-line);
/* .prayer-row.header color: */ color: var(--muted-fg);
/* .prayer-row.current: add accent ring (invisible when --accent is transparent): */
.prayer-row.current {
  position: relative;
  z-index: 1;
  color: var(--ink);
  background: #fff;
  border-bottom: 0;
  box-shadow: inset 6px 0 0 var(--accent);
}
/* .additional-times background: */ background: var(--green-strip);
/* .additional-time border-right: */ border-right: 1px solid var(--green-line);
/* .clock-block: */ background: #fff; color: var(--ink);
/* .mosque-logo color: */ color: var(--white);
/* .countdown-block border-top: */ border-top: 2px solid var(--green-line);
```

- [ ] **Step 3: Append the overlay rules at the end of `app.css`**

```css
/* Presentation overlays: palette rebinds surface tokens; font picks the
   stack (Outfit ships in static/fonts); density-compact tightens rhythm. */
body.palette-midnight {
  --green: #0b0f0e;
  --green-line: #2e3a36;
  --green-strip: rgba(0, 0, 0, .45);
  --white: #f2f2f2;
  --ink: #0b0f0e;
  --glow: rgba(0, 0, 0, .5);
  --cell-line: rgba(120, 135, 130, .5);
  --muted-fg: rgba(242, 242, 242, .75);
  --grad-a: #0b0f0e;
  --grad-b: #101615;
  --grad-c: #0b0f0e;
  --accent: #c9a227;
}

body.palette-sand {
  --green: #ece4cf;
  --green-line: #c2b694;
  --green-strip: rgba(120, 100, 60, .16);
  --white: #33302a;
  --ink: #33302a;
  --glow: rgba(150, 130, 80, .25);
  --cell-line: rgba(120, 110, 85, .55);
  --muted-fg: rgba(51, 48, 42, .72);
  --grad-a: #e7dcc2;
  --grad-b: #ece4cf;
  --grad-c: #e3d7ba;
  --accent: #8a6d1f;
}

@font-face {
  font-family: Outfit;
  src: url(fonts/Outfit-400.woff2) format("woff2");
  font-weight: 400;
  font-display: swap;
}
@font-face {
  font-family: Outfit;
  src: url(fonts/Outfit-700.woff2) format("woff2");
  font-weight: 700;
  font-display: swap;
}
@font-face {
  font-family: Outfit;
  src: url(fonts/Outfit-800.woff2) format("woff2");
  font-weight: 800;
  font-display: swap;
}

body.font-outfit { font-family: Outfit, Arial, Helvetica, sans-serif; }
body.font-system { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; }

body.density-compact .prayer-screen {
  padding: clamp(16px, 2vw, 36px) clamp(16px, 1.8vw, 32px);
  gap: clamp(22px, 2.2vw, 36px);
}
body.density-compact .prayer-row { min-height: clamp(60px, 5vw, 84px); }
body.density-compact .additional-time { min-height: clamp(72px, 6vw, 108px); }
body.density-compact .clock { font-size: clamp(38px, 4.8vw, 80px); }
```

- [ ] **Step 4: Run the stylesheet tests**

Run: `uv run pytest tests/test_display_stylesheet.py -q`
Expected: PASS (6 passed). Note: `test_rules_consume_tokens_not_hardcoded_hex` still expects `--custom-` absent — that assertion goes green only after Task 4; before then it FAILS. If it fails now, proceed to Task 4 (do not commit yet).

- [ ] **Step 5: Commit — only after Task 4 makes the whole file green**

(Commit happens in Task 4 Step 4.)

---

### Task 4: Template `custom_colors` rebinds surface tokens

**Files:**
- Modify: `src/muhideen/views/templates/display.html:7`
- Test: `tests/test_display_from_files.py:98-103`, `tests/test_display_stylesheet.py`

- [ ] **Step 1: Change the emission to rebind surface tokens**

Replace `display.html:7`'s `<style>` fragment (keep the surrounding `<link>` and `{% if %}` logic identical). Literal CSS braces use `{{ '{' }}` / `{{ '}' }}` — a bare `{` would merge with the Jinja `{%` delimiter (this was the Phase 0 syntax bug: rendered output was `:root--custom-…` with no braces):

```html
<link rel="stylesheet" href="/static/app.css">{% if custom_colors %}<style>body{{ '{' }}{% for k, v in custom_colors.items() %}{% if k == "background" %}--green:{{ v }} !important;{% elif k == "foreground" %}--white:{{ v }} !important;{% elif k == "accent" %}--accent:{{ v }} !important;{% endif %}{% endfor %}{{ '}' }}</style>{% endif %}
```

Unknown keys emit nothing (the model constrains keys to the three, so the `elif` chain is exhaustive in practice and fail-closed in template).

- [ ] **Step 2: Update the emission test to the rebind contract**

Replace `test_custom_colors_style_only_when_set` in `tests/test_display_from_files.py:98-103` with:

```python
def test_custom_colors_style_only_when_set(file_client: TestClient) -> None:
    en_html = file_client.get("/display", params={"id": "main-hall"}).text
    assert "<style>body" not in en_html
    ms_html = file_client.get("/display", params={"id": "entrance"}).text
    assert "<style>body" in ms_html
    assert "--green:#0b0f0e!important" in ms_html.replace(" ", "")
    assert "--white:#f2f2f2!important" in ms_html.replace(" ", "")
    assert "--accent:#c9a227!important" in ms_html.replace(" ", "")
    assert "--custom-" not in ms_html
```

(The `entrance` example sets all three custom colors: background `#0b0f0e`, foreground `#f2f2f2`, accent `#c9a227`.)

- [ ] **Step 2b: Add the divergent-override regression test** (a custom color that differs from the palette must win — the entrance fixture duplicates midnight hexes and would mask shadowing):

```python
def test_custom_colors_override_palette(tmp_path: Path) -> None:
    """Operator custom colors beat the palette even when they differ."""
    import json as _json
    import shutil as _shutil

    from muhideen.adapters.file_config import (
        FilePlaylistRepo as _FilePlaylistRepo,
        FilePrayerRepo as _FilePrayerRepo,
        FileSettingsRepo as _FileSettingsRepo,
        load_config_file as _load_config,
    )
    from muhideen.adapters.sse_bus import SSEBus as _SSEBus
    from muhideen.api.app import AppDeps as _AppDeps
    from muhideen.api.app import create_app as _create_app

    dest = tmp_path / "muhideen.json"
    _shutil.copy(EXAMPLE, dest)
    raw = _json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    raw["displays"]["entrance"]["custom_colors"]["background"] = "#123456"
    dest.write_text(_json.dumps(raw, indent=2) + "\n")
    cfg = _load_config(dest)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    deps = _AppDeps(
        settings_repo=_FileSettingsRepo(dest),
        prayer_repo=_FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=_SSEBus(),
        playlist_repo=_FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
    )
    with TestClient(_create_app(deps)) as client:
        html = client.get("/display", params={"id": "entrance"}).text
    assert "--green:#123456!important" in html.replace(" ", "")
```

- [ ] **Step 3: Run the display tests**

Run: `uv run pytest tests/test_display_from_files.py tests/test_display_stylesheet.py tests/unit/test_display_context.py -q`
Expected: PASS.

- [ ] **Step 4: Commit Tasks 3+4**

```bash
git add src/muhideen/static/app.css src/muhideen/views/templates/display.html tests/test_display_from_files.py tests/test_display_stylesheet.py
git commit -m "feat(display): wire presentation knobs to stylesheet variables (#90)"
```

---

### Task 5: Pin the unified countdown format (no visual fork)

**Files:**
- Modify: `tests/test_display_from_files.py` (append test)
- Test: same file

- [ ] **Step 1: Add the pin test**

```python
def test_countdown_format_unified_across_styles(tmp_path: Path) -> None:
    """countdown_style is accepted for compat; the screen renders the single
    design-locked HH:MM:SS format either way (no boxes/inline fork)."""
    import json as _json
    import shutil as _shutil

    from muhideen.adapters.file_config import (
        FilePlaylistRepo as _FilePlaylistRepo,
        FilePrayerRepo as _FilePrayerRepo,
        FileSettingsRepo as _FileSettingsRepo,
        load_config_file as _load_config,
    )
    from muhideen.adapters.sse_bus import SSEBus as _SSEBus
    from muhideen.api.app import AppDeps as _AppDeps
    from muhideen.api.app import create_app as _create_app

    bodies = []
    for style in ("boxes", "inline"):
        dest = tmp_path / f"muhideen-{style}.json"
        _shutil.copy(EXAMPLE, dest)
        raw = _json.loads(dest.read_text())
        raw["schedule"]["lat"] = 3.07
        raw["schedule"]["lon"] = 101.69
        raw["theme"]["countdown_style"] = style
        dest.write_text(_json.dumps(raw, indent=2) + "\n")
        cfg = _load_config(dest)
        media = tmp_path / f"media-{style}"
        media.mkdir(exist_ok=True)
        deps = _AppDeps(
            settings_repo=_FileSettingsRepo(dest),
            prayer_repo=_FilePrayerRepo(
                tmp_path / f"buffer-{style}.json", cfg.schedule.manual_days
            ),
            clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
            event_bus=_SSEBus(),
            playlist_repo=_FilePlaylistRepo(dest),
            media_dir=media,
            config_path=dest,
        )
        with TestClient(_create_app(deps)) as client:
            bodies.append(client.get("/display", params={"id": "main-hall"}).text)
    assert (
        bodies[0].count('class="countdown"')
        == bodies[1].count('class="countdown"')
        == 1
    )
    assert "countdown-inline" not in bodies[0] and "countdown-inline" not in bodies[1]
```

- [ ] **Step 2: Run to verify it passes (pin, not TDD-red)**

Run: `uv run pytest tests/test_display_from_files.py::test_countdown_format_unified_across_styles -q`
Expected: PASS (documents current behavior; fails if anyone forks the format without updating the test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_display_from_files.py
git commit -m "test(display): pin unified countdown format across styles (#90)"
```

---

### Task 6: Playwright dependency + computed-style browser test

**Files:**
- Modify: `pyproject.toml`, `.github/workflows/ci.yml`, `TESTING_STRATEGY.md`
- Create: `tests/browser/__init__.py`, `tests/browser/test_display_computed.py`
- Test: `tests/browser/test_display_computed.py`

- [ ] **Step 1: Add the dependency and marker**

In `pyproject.toml` `[dependency-groups] dev`, append `"playwright>=1.49",` (keep list sorted as-is; append at end after `import-linter`). In `[tool.pytest.ini_options] markers`, append `"browser: headless Chromium computed-style checks",`.

- [ ] **Step 2: Write the browser test (expect red: missing dep)**

```python
"""Computed-style checks for presentation wiring (#90).

Loads the real display HTML with the real stylesheet: the rendered page is
written to a `tmp_path` `.html` file and opened via its `file://` URL, so
headless Chromium applies the true cascade including the `file://`
stylesheet link (note: `page.set_content(html)` does NOT work — Chromium
blocks `file://` subresources from an `about:blank` page; verified
2026-10-07). No test server, no ports. Skips when Playwright/Chromium is
unavailable so unit/e2e stays green on machines without browsers.
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
    # Opened as a file:// page (not set_content: Chromium blocks file://
    # subresources from about:blank) so the file:// stylesheet truly applies.
    return html.replace("/static/app.css", APP_CSS.as_uri())


def _background_rgb(
    page: object, tmp_path: Path, display_id: str, custom_bg: str | None = "#123456"
) -> str:
    from playwright.sync_api import sync_playwright

    html = _rendered_html(tmp_path, display_id, custom_bg)
    # Written to disk and opened as file:// (set_content would block the
    # file:// stylesheet from about:blank).
    doc = tmp_path / f"{display_id}.html"
    doc.write_text(html)
    with sync_playwright() as browser_host:
        browser = browser_host.chromium.launch()
        try:
            page_handle = browser.new_page()
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
```

Note: `page` parameter is unused by design (keeps signature parity with Playwright fixtures); the helper drives the browser explicitly. Body background resolves through `.prayer-screen`? No — assertion reads `document.body` background: body rule `background: var(--green)` → midnight `rgb(11,15,14)` (#0b0f0e), classic `rgb(7,87,67)` (#075743).

- [ ] **Step 3: Install and run (expect red → green after `uv sync`)**

Run: `uv sync --locked --all-extras` then `uv run playwright install --with-deps chromium` (sudo available on CI runners; locally skip `--with-deps` if deps exist: `uv run playwright install chromium`).
Then: `uv run pytest tests/browser -q`
Expected before dep install: SKIPPED; after: PASS (2 passed).

- [ ] **Step 4: Wire CI + strategy docs**

In `.github/workflows/ci.yml` after the `uv sync` step, insert:
```yaml
      - run: uv run playwright install --with-deps chromium
```
In `TESTING_STRATEGY.md`, append a row to the test-location table:
`| tests/browser/ | browser | Headless Chromium computed-style checks (presentation wiring); file:// stylesheet, no test server. |`
Keep table column widths as-is otherwise.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/ci.yml TESTING_STRATEGY.md tests/browser/
git commit -m "test(display): Playwright computed-style checks for themes (#90)"
```

---

### Task 7: CHANGELOG + full gate

**Files:**
- Modify: `CHANGELOG.md`
- Test: full suite

- [ ] **Step 1: Add the CHANGELOG entry**

Read the head of `CHANGELOG.md`; under `[Unreleased] Added` (create the section if absent, matching existing format) add:
`Display presentation knobs wired to the stylesheet (palette/font/density palettes render; custom_colors rebinds surface tokens; unified countdown format pinned) (#90).`

- [ ] **Step 2: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q`
Expected: all green. (CI adds coverage/theme-lint; run `uv run pytest -q --cov=muhideen --cov-report=term-missing` and `uv run tools/lint_theme.py --theme classic-green` if time permits — coverage `fail_under=95` must hold.)

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for display presentation wiring (#90)"
```

---

### Task 8: BM test asserts per-row labels (#61)

**Files:**
- Modify: `tests/test_display_from_files.py:84-89`
- Test: same file

- [ ] **Step 1: Strengthen to per-element assertions**

Replace `test_ms_display_renders_midnight_and_malay` with:

```python
def _row_segment(html: str, row_id: str) -> str:
    """HTML slice belonging to one prayer row (up to the next row/bound)."""
    parts = html.split(f'id="{row_id}"')
    assert len(parts) == 2, f"expected exactly one element with {row_id}"
    tail = parts[1].split('id="row-')[0].split('id="bound-')[0]
    return tail


def test_ms_display_renders_midnight_and_malay(file_client: TestClient) -> None:
    html = file_client.get("/display", params={"id": "entrance"}).text
    assert "palette-midnight" in html
    assert 'lang="ms"' in html
    fajr_row = _row_segment(html, "row-fajr")
    assert "Subuh" in fajr_row
    assert "Zohor" not in fajr_row
    dhuhr_row = _row_segment(html, "row-dhuhr")
    assert "Zohor" in dhuhr_row
    assert "Subuh" not in dhuhr_row
```

Rationale (record in commit message): the redesign renders only the primary name by design, so triple-adjacency rendering is out of scope; per-row primary assertions close the real blind spot (swapped rows pass a presence check). The `ms==PRAYER_LABELS[2]` pin and `ms.json` honesty check stay untouched.

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/test_display_from_files.py -q`
Expected: PASS.

- [ ] **Step 3: Sanity-check the blind spot is actually closed (mutation check)**

Run: temporarily swap two BM labels in `src/muhideen/views/display.py` (`"Subuh"` ↔ `"Zohor"`), run `uv run pytest tests/test_display_from_files.py::test_ms_display_renders_midnight_and_malay -q`, expect FAIL, then revert with `git checkout -- src/muhideen/views/display.py`. Do not commit the mutation.

- [ ] **Step 4: Commit**

```bash
git add tests/test_display_from_files.py
git commit -m "test(display): per-row BM label assertions (#61)"
```

---

## Self-review

1. **Spec coverage:** #90 custom_colors → Tasks 2–4 (consumers + rebind emission); theme overlays → Task 3 (palette/font/density rules); "decide which custom properties the template may emit" → rebind table + Task 4; "browser-level check" → Task 6. #61 triple-adjacency re-analysis → Task 1 Step 3 + Task 8 (Option A with recorded rationale; Option B explicitly out of scope per the design lock).
2. **Placeholder scan:** all hex values, selectors, commands, and test bodies are exact. The only intentionally adjustable items are palette hex values pending mockup verification (Task 1 Step 4 locks structure regardless).
3. **Type consistency:** no Python signatures change; `theme_css_class` output unchanged so `test_display_context.py:210-240` pins hold; `custom_colors` stays `dict[str,str]|None`; `body_class` classes consumed in CSS are exactly `palette-{classic-green,midnight,sand}`, `font-{outfit,system}`, `density-{comfortable,compact}`.
