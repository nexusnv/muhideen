# Pre-flight Phase-1B Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enter Phase 2 with zero open 1B issues, English-only UI with a file-based translation foundation, and stamped specs.

**Architecture:** Locked user decisions: Arabic prayer names are invariant UI (always rendered, never translated); prayer EN names plus all admin/display chrome go English-only now; future languages add `locales/<lang>.json` files (shape pinned by `locales/en.json`, kept honest by test). Issue #16 closes as delivered-for-display; #20 gets the minimal honest fix (hero tomorrow-annotation, timetable untouched).

**Tech Stack:** Jinja2 templates, vanilla JS, pytest, `gh` CLI for issue ops.

---

## File map

- Modify: `src/muhideen/views/display.py`, `src/muhideen/views/templates/display.html`, `src/muhideen/views/templates/admin/login.html`, `setup.html`, `settings.html`, `src/muhideen/static/admin.js`, `tests/unit/test_display_static.py`, `tests/unit/test_admin_static.py`, `tests/unit/test_display_states.py`, `tests/unit/test_display_context.py`, `tests/e2e/test_display.py`, `tests/e2e/test_admin_pages.py`, `docs/development/specs/2026-09-25-1b-frontend-design.md`, `docs/development/PHASES_AND_SLICES.md`, `CHANGELOG.md`
- Create: `locales/en.json`, `locales/README.md`, `tests/unit/test_locales.py`

---

### Task 1: English-only strip (prayer labels stay EN + Arabic)

**Files:**
- Modify: `src/muhideen/views/display.py`, `src/muhideen/views/templates/display.html`, `src/muhideen/views/templates/admin/login.html`, `setup.html`, `settings.html`, `src/muhideen/static/admin.js`, `tests/unit/test_display_states.py`, `tests/unit/test_display_context.py`, `tests/unit/test_display_static.py`, `tests/unit/test_admin_static.py`, `tests/e2e/test_display.py`, `tests/e2e/test_admin_pages.py`

Rules: Arabic strings are byte-identical everywhere (never touched). Every BM string becomes its EN equivalent (admin BM→EN map: Kata Laluan→Password, Log Masuk→Login, Simpan→Save, Tetapan→Settings, Persediaan→Setup, Nama Masjid→Masjid name, Sahkan kata laluan→Confirm password, Kembali→Back, Seterusnya→Next, Selesai→Done, Semak input→Check input, Persediaan gagal→Setup failed, Tetapan tidak sah (422)→Invalid settings (422), Ralat rangkaian→Network error, Kata laluan salah→Wrong password, Terlalu banyak cubaan, tunggu sebentar→Too many attempts, wait a minute, Disimpan — dimuat semula→Saved — live reload, Pengiraan luar talian sahaja→Offline calculation only, Kira detik sempadan→Boundary countdown, Tunjuk QR sambungan→Show connect QR). Prayer BM column deleted (Subuh/Zohor/Asar/Maghrib/Isyak/Jumaat gone; EN+AR remain).

- [ ] **Step 1: Update the tests first (red)**

In each test file replace BM assertions with EN ones (exact swaps):
- `tests/e2e/test_display.py`: `("Subuh", "Zohor", "Isyak", "الفجر", "المغرب")` → `("Fajr", "Dhuhr", "Isha", "الفجر", "المغرب")`; `"Jumaat"` → `"Jumuah"`.
- `tests/e2e/test_admin_pages.py`: `"Kata Laluan"` → `"Password"`; `"Persediaan"` → `"Setup"`; drop `data-en=` assertions (assert plain English instead).
- `tests/unit/test_admin_static.py`: replace `data-en`/`lang-toggle` presence asserts with absence asserts (`assert "data-en" not in html`, `assert "lang-toggle" not in js-or-html`) plus English presence (`"Password"`, `"Save"`).
- `tests/unit/test_display_states.py`, `test_display_context.py`: drop any `bm`/`next_name_bm` assertions; assert `next_name_en`/`next_name_ar` only. (Read each file first; change only BM-touching lines.)

Run: `uv run pytest tests/e2e/test_display.py tests/e2e/test_admin_pages.py tests/unit/test_admin_static.py tests/unit/test_display_states.py tests/unit/test_display_context.py -q`
Expected: FAIL (templates still BM).

- [ ] **Step 2: Strip builder + templates + JS**

`src/muhideen/views/display.py`: `PRAYER_LABELS`/`BOUNDARY_LABELS` become 2-tuples `(en, ar)` (Arabic values byte-identical); cards use `"en"` + `"ar"` keys only (delete `"bm"`); `labels[1]`→`labels[1]` still Arabic (index shift: old `labels[2]` → new `labels[1]` — update `next_name_ar` and any `labels[1]` BM use); delete any `next_name_bm` key; `build_display_context` otherwise unchanged.

`display.html`: `{{ c.en }} · {{ c.bm }} · {{ c.ar }}` → `{{ c.en }} · {{ c.ar }}`; `{{ next_name_en }} · {{ next_name_bm }} · {{ next_name_ar }}` → `{{ next_name_en }} · {{ next_name_ar }}` (both occurrences: hero-next and overlay-adhan; dim uses en+ar already — verify).

Admin templates (all 3): every `data-en="X">BM` becomes plain `X` (EN text, no attrs); delete all `<button id="lang-toggle">` elements; `<html lang="bm">` → `<html lang="en">`.

`admin.js`: delete `setLang` + toggle listener; STR map deleted, all `t("...")` calls replaced with the EN literals from the map (map is bilingual source — copy the `en:` values verbatim).

- [ ] **Step 3: Run green**

Run the Step-1 command. Expected: all PASS. Then ruff/format/pyright on touched Python files.

- [ ] **Step 4: Commit**

```bash
git add src/muhideen/views/display.py src/muhideen/views/templates/display.html src/muhideen/views/templates/admin/ src/muhideen/static/admin.js tests/unit/test_display_states.py tests/unit/test_display_context.py tests/unit/test_display_static.py tests/unit/test_admin_static.py tests/e2e/test_display.py tests/e2e/test_admin_pages.py
git commit -m "feat: English-only UI, Arabic prayer names invariant"
```

---

### Task 2: File-based translation foundation

**Files:**
- Create: `locales/en.json`, `locales/README.md`, `tests/unit/test_locales.py`

Key set (owner file in brackets; copy exact EN values from source — the honesty test fails loudly on any miscopy, which is the verification):
- `prayer`: fajr/dhuhr/asr/maghrib/isha/jumuah EN names [`views/display.py` PRAYER_LABELS col 0]
- `boundary`: imsak/syuruq/dhuha EN names [`views/display.py` BOUNDARY_LABELS col 0]
- `display`: pre_note template `"Preparing for {name}"` (store with `{name}` placeholder) [`views/display.py`], banners (`STALE — showing fallback schedule`, `TIME UNSYNCED`, `CALC — computed schedule`, `MANUAL — set by admin`) [`views/display.py`], skip hint (`Hold 3s to restore (admin)`) [`display.html`], qr hint (`Admin: muhideen.local:8000/admin`) [`display.html`], bound-next uses marker key (no string) — skip
- `admin_login`: title/button (`Login`), password (`Password`), wrong (`Wrong password`), limited (`Too many attempts, wait a minute`), net (`Network error`) [`login.html` + `admin.js` STR-en]
- `admin_setup`: title (`Setup`), name (`Masjid name`), next/done/back (`Next`/`Done`/`Back`), confirm (`Confirm password`), check (`Check input`), setupFail (`Setup failed`), invalid (`Invalid settings (422)`), close note (`Setup closes after the first admin is created.`) [`setup.html` + `admin.js`]
- `admin_settings`: title (`Settings`), calc-only (`Offline calculation only`), boundary (`Boundary countdown`), save (`Save`), saved (`Saved — live reload`), qr toggle (`Show connect QR`) [`settings.html` + `admin.js`]

- [ ] **Step 1: Write the honesty test first**

```python
"""Locale table honesty: every en.json value lives in its owner (pre-flight)."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "locales"

OWNERS = {
    "prayer": ["src/muhideen/views/display.py"],
    "boundary": ["src/muhideen/views/display.py"],
    "display": [
        "src/muhideen/views/display.py",
        "src/muhideen/views/templates/display.html",
    ],
    "admin_login": [
        "src/muhideen/views/templates/admin/login.html",
        "src/muhideen/static/admin.js",
    ],
    "admin_setup": [
        "src/muhideen/views/templates/admin/setup.html",
        "src/muhideen/static/admin.js",
    ],
    "admin_settings": [
        "src/muhideen/views/templates/admin/settings.html",
        "src/muhideen/static/admin.js",
    ],
}


def _walk(node: object) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_walk(value))
        return out
    assert isinstance(node, str)
    return [node]


def test_every_english_string_lives_in_its_owner() -> None:
    table = json.loads((LOCALES / "en.json").read_text())
    assert set(table) == set(OWNERS)
    for group, files in OWNERS.items():
        haystack = "".join((ROOT / f).read_text() for f in files)
        values = [v for v in _walk(table[group]) if "{name}" not in v]
        assert values, f"empty group {group}"
        for value in values:
            assert value in haystack, f"{value!r} of [{group}] not found in owners"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_locales.py -q`
Expected: FAIL with `FileNotFoundError` (no locales dir).

- [ ] **Step 3: Write `locales/en.json` + `locales/README.md`**

Build en.json with the exact key set above (values copied verbatim from the owner files — the test is the verifier). README (complete):

```markdown
# Locales (future translation seam)

`en.json` is the full English string table for the UI. Arabic prayer names
are NOT in the table: they are invariant UI, rendered always regardless of
language.

## Adding a language

1. Copy `en.json` to `<lang>.json` (same keys, translated values; keep `{name}` placeholders).
2. Wire a loader: templates need a `T()` Jinja global reading the active table with en-fallback; `admin.js` needs the table embedded (render as JSON in a `<script>` block or a `GET /api/strings?lang=` endpoint — endpoint is a contract addition, decide then).
3. Re-point the honesty test at every table (parametrize `en.json` → all `*.json`).
4. Add a language switcher back to the three admin templates (removed in the English-only strip; see git history).
```

- [ ] **Step 4: Run green + commit**

Run: `uv run pytest tests/unit/test_locales.py -q` (PASS) + ruff/format on the test.
```bash
git add locales/ tests/unit/test_locales.py
git commit -m "feat: file-based translation foundation (en table)"
```

---

### Task 3: #20 hero tomorrow-annotation

**Files:**
- Modify: `src/muhideen/views/display.py`, `src/muhideen/views/templates/display.html`, `tests/unit/test_display_states.py`, `tests/e2e/test_display.py`
- Test: same test files

Rationale (locked): the timetable stays one coherent day; the hero names the exception. Full tomorrow-cards resolution would duplicate a day fetch + builder path for a 1-minute edge.

- [ ] **Step 1: Write the failing tests**

Builder test (append to `tests/unit/test_display_states.py`):

```python
def test_next_tomorrow_flag_when_adhan_is_next_day() -> None:
    from datetime import datetime

    from muhideen.views.display import build_display_context

    day = _day()
    event = _event(
        "NORMAL",
        now=datetime(2025, 10, 20, 21, 0, tzinfo=KL),
        next_prayer="fajr",
        adhan_at=datetime(2025, 10, 21, 5, 45, tzinfo=KL),
    )
    ctx = build_display_context(day=day, event=event, settings=_settings())
    assert ctx["next_tomorrow"] is True


def test_next_tomorrow_false_same_day() -> None:
    from muhideen.views.display import build_display_context

    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )
    assert ctx["next_tomorrow"] is False
```

(Uses that file's existing `_day`/`_event`/`_settings` helpers — verify names match; they do per Task 1 of the 1B-2 plan. `_event` accepts overrides — verified in the committed file before writing.)

e2e (append to `tests/e2e/test_display.py`):

```python
def test_hero_marks_tomorrow_fajr(surface: SimpleNamespace, client: TestClient) -> None:
    from datetime import datetime

    _seed_settings(surface)
    _advance_to(surface, datetime(2025, 10, 20, 21, 0))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="next-tomorrow"' in html
```

(Uses that file's `_seed_settings`/`_advance_to` — verify before writing.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_display_states.py -q -k tomorrow; uv run pytest tests/e2e/test_display.py -q -k tomorrow`
Expected: FAIL with `KeyError: 'next_tomorrow'`.

- [ ] **Step 3: Implement**

`display.py`: in `build_display_context`, compute `adhan_date = event.adhan_at.date() if event.adhan_at else None` and add `"next_tomorrow": adhan_date is not None and adhan_date > day.date,`.

`display.html` hero-next block:

```html
    <div id="next-time">{{ next_time }}{% if next_tomorrow %} <span id="next-tomorrow">(tomorrow)</span>{% endif %}</div>
```

- [ ] **Step 4: Run green + commit**

Run both test files green + ruff/format/pyright.
```bash
git add src/muhideen/views/display.py src/muhideen/views/templates/display.html tests/unit/test_display_states.py tests/e2e/test_display.py
git commit -m "fix: mark tomorrow Fajr in display hero (closes #20)"
```

---

### Task 4: Issue + spec housekeeping (docs + `gh`)

**Files:**
- Modify: `docs/development/specs/2026-09-25-1b-frontend-design.md` (status line), `docs/development/PHASES_AND_SLICES.md` (landed markers), `CHANGELOG.md`
- Test: none (docs + issue ops; verify with commands)

- [ ] **Step 1: Close #16 as delivered**

Run:
```bash
gh issue comment 16 --repo nexusnv/muhideen --body "Delivered for the display need by 1B-1 (hijridate 2.6.0, additive hijri_date on prayer-day with golden + offset tests, fixture + contract + changelog). The remaining deltas (hijri on next-event/SSE, swappable Umm al-Qura/tabular port, absolute goldens at all four offset edges) have no consumer — reopen narrowed if one appears."
gh issue close 16 --repo nexusnv/muhideen --reason completed
```
Expected: issue closed.

- [ ] **Step 2: Close #20 after Task 3 lands**

Run:
```bash
gh issue comment 20 --repo nexusnv/muhideen --body "Fixed by hero tomorrow-annotation (timetable stays one coherent day); verified by builder + e2e tests."
gh issue close 20 --repo nexusnv/muhideen --reason completed
```
Expected: issue closed. (Do this only after Task 3's commit exists on the branch.)

- [ ] **Step 3: Stamp the spec + slices + changelog**

Spec `2026-09-25-1b-frontend-design.md` line 3 status: replace `Status: design approved section-by-section (§1–§7) in brainstorming, 2026-09-25.` with `Status: delivered by 1B-1/1B-2/1B-3 (merged); English-only per pre-flight (BM toggle removed); manual browser checklist (§8/§10) still open — owner sign-off required.`

`PHASES_AND_SLICES.md`: append ` (landed)` to the three 1B row headers: `| 1B-1 Display \`classic-green\` (landed) |`, `| 1B-2 States + dim (landed) |`, `| 1B-3 Admin wizard (landed) |`. (Exact old strings: `| 1B-1 Display \`classic-green\` |`, `| 1B-2 States + dim |`, `| 1B-3 Admin wizard |` — replace the header cell only.)

`CHANGELOG.md` `[Unreleased]`: add `### Changed` entry if the section exists, else create it: `- UI is English-only (BM toggle removed); Arabic prayer names stay invariant; \`locales/en.json\` seeds file-based translation. Display hero marks tomorrow Fajr.`

- [ ] **Step 4: Verify + commit**

Run: `gh issue view 16 --json state --jq .state` → CLOSED; same for 20. `rg -n "data-en|Subuh|Jumaat|Kata Laluan|Persediaan|Tetapan|Log Masuk|Simpan" src/muhideen/views/ src/muhideen/static/admin.js` → 0 hits (no BM remnants outside locales/en.json values... note: en.json holds EN only, so 0 hits for those BM tokens repo-wide excluding docs/history).
```bash
git add docs/development/specs/2026-09-25-1b-frontend-design.md docs/development/PHASES_AND_SLICES.md CHANGELOG.md
git commit -m "chore: pre-flight housekeeping (issues, spec status, changelog)"
```

---

### Task 5: Full gate

**Files:** none (verification only; fix fallout minimally if the gate finds pre-flight-caused failures, else BLOCKED)

- [ ] **Step 1: Run the gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q`
Expected: all green. (Format the pre-flight plan doc too if flagged — same lesson as 1B-1/1B-2/1B-3.)

Run: `uv run pytest --cov=muhideen --cov-report=term-missing`
Expected: ≥95.

- [ ] **Step 2: Manual-checklist handoff note**

No commit. Report the gate outputs plus this explicit handoff: the spec §8/§10 manual browser checklist (legibility at 1080p, AA visual, 5-state walkthrough, dim skip feel, mobile wizard, QR scan, forced banners, resolutions) requires a device/Chromium and CANNOT be done here — owner sign-off is the last open exit item before Phase 2.

---

## Self-review

1. **Spec coverage:** user decisions all mapped (AR invariant → Task 1 exclusions; JSON+test → Task 2; close #16 → Task 4; fix #20 → Task 3). Pre-flight asks (spec comparison → Task 4 stamps; issues → Task 4; i18n → Tasks 1–2) all owned.
2. **Placeholder scan:** exact code/commands throughout; adaptation points explicit (helper-name verification, plan-doc formatting).
3. **Type consistency:** `next_tomorrow` bool in builder/template/tests; `locales/en.json` key set == honesty OWNERS keys; BM→EN map applied uniformly; `gh issue close --reason completed` valid flag.
