# Display screen redesign (mockup) — Implementation Plan (user-directed, no issue)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: plan self-review below, `implementation-plan-review` before build, `oracle-review` on the branch diff before PR handoff.

**Goal:** Replace the public display screen with the approved mockup layout (timetable + clock/dates/mosque/countdown two-column), backend unchanged — all context keys stay, template renders a subset.

**Architecture:** `views/display.py` gains additive 12h + countdown + long-date keys (existing keys byte-identical); `display.html` rewritten to the mockup structure with Jinja bindings; `app.css` rewritten to the mockup stylesheet (fixed green, Arial, responsive breakpoints kept); `app.js` display script rewritten around the new hooks (design-locked 12h HH:MM clock with blinking colon, plain HH:MM:SS countdown, retained SSE/poll/heartbeat/skip/audio); theme `clock_format` default flips to `12h` (VO + fixtures + migration `0004` updating seeded `24h-seconds` rows — pre-1.0, no production installs; knob stays settable for future surfaces, screen ignores it by design). Deliberately dropped from render (backend keeps computing): ar/bm names, hero boxes, banners→minimal strip only when present (kept), pre-note, tomorrow marker, dim page (SALAH_DIM renders the same layout dimmed with skip kept), Main Stage/playlist surfaces.

**Tech Stack:** Python 3.11+, Jinja, vanilla CSS/JS, pytest markers `unit/e2e`, Playwright screenshot verification.

**References:** `new_screen.png` (mockup image, repo root — delete before PR); user HTML/CSS in the request message; `src/muhideen/views/display.py` (builder); `src/muhideen/views/templates/display.html:1-96`; `src/muhideen/static/app.css:1-94`; `src/muhideen/static/app.js:1-172`; `src/muhideen/core/values.py` (`THEME_DEFAULTS` clock_format); `api/fixtures/settings.json` (theme.clock_format); `tests/e2e/test_display.py` (region/id assertions); `tests/unit/test_display_context.py` (clock/ctx assertions).

**Branch:** `feature/display-redesign` (base on `main`)

---

## Background the implementer needs

### Locked behavior (user-specified)

- Countdown block: `NORMAL`/`PRE_ADHAN` → `"<EN> call to prayer in"` + HH:MM:SS to `adhan_at`; `IQAMAH_COUNTDOWN`/`ADHAN` → `"Iqomah in"` + HH:MM:SS to `iqamah_at` (blank when target missing); `SALAH_DIM`/other → block omitted entirely (blank area).
- Clock: HH:MM 12h always, no seconds, colon blinks every second (CSS animation on a `.colon` span; JS re-renders each tick).
- Timetable: header Prayer/Adhan/Iqomah; 5 rows from `cards` (backend EN name as-is — `Dhuhr` stays, mockup `Zuhr` is placeholder data); adhan + iqamah columns in 12h (`h:MM` + small AM/PM); `is_next` row gets `.current`.
- Bounds strip keeps backend EN names (`Syuruq` stays) in 12h; mosque block shows `masjid_name` + `zone` (no address fields exist — backend unchanged); logo SVG copied from mockup.
- Countdown format is always `HH:MM:SS` with leading `00:` (no boxes/labels).
- `12h` becomes the theme default (user: "12h is default"); screen itself is design-locked 12h regardless of knob.

### Preserved behaviors (not in mockup, kept)

- SSE state/tick reload, 60s poll fallback, 30s heartbeat (same endpoints).
- Hidden `#hero-clock` data span (`data-now/state/next/adhan/tz/tzoffset`) — JS anchor + test hook.
- Adhan audio tag in ADHAN state when `adhan_audio_url` present + JS volume/play.
- SALAH_DIM renders the same layout with a dimming class + long-press skip via `data-dim-until` (no separate dim page).
- Minimal banner strip only when `banners` non-empty (abnormal states only; normal render pixel-matches mockup).
- `body_class`, per-card `iqamah-{key}` ids, `bound-{key}` ids kept as hooks.

## File Structure

- Modify: `src/muhideen/views/display.py` — `twelve_h()` helper + `time12/period/iqamah12/iqamah_period` on cards/bounds + `gregorian_long` + `countdown_label/countdown_target` + `clock` stays (JS overrides; server fallback 12h HH:MM with colon span)
- Modify: `src/muhideen/core/values.py` — `THEME_DEFAULTS["clock_format"] = "12h"`
- Create: `src/muhideen/migrations/0004_theme_clock_default.sql` + `.down.sql` — seeded `24h-seconds` → `12h`
- Modify: `api/fixtures/settings.json` (+ any theme fixture pinning `24h-seconds`) — default flip ripple
- Rewrite: `src/muhideen/views/templates/display.html` — mockup structure + bindings (delete `new_screen.png` at the end)
- Rewrite: `src/muhideen/static/app.css` — mockup stylesheet (drop webfont/theme-variant rules; keep `.slate-error`)
- Rewrite: `src/muhideen/static/app.js` display parts — keep SSE/poll/heartbeat/skip/audio skeletons, new clock/countdown renderers
- Update: `tests/e2e/test_display.py` — new-structure assertions (regions, current row, 12h times, countdown label/target, blank-in-dim, tz attrs, iqamah elements, banner-strip-only-when-present)
- Update: `tests/unit/test_display_context.py` — new keys + 12h vectors + label matrix + default-clock test updates
- Verify: Playwright screenshot of `/display` vs mockup + full gate

No DTO/fixture-shape/contract change (additive ctx keys only), no admin change; one kv settings migration (seeded default only).

---

### Task 1: Builder 12h + countdown + long-date keys

**Files:** `src/muhideen/views/display.py`, `tests/unit/test_display_context.py`

**Goal:** Template gets everything preformatted; existing keys untouched.

- [ ] Failing tests: `twelve_h` vectors (`05:48→5:48 AM`, `12:45→12:45 PM`, `00:15→12:15 AM`, `18:05→6:05 PM`); cards carry `time12/period/iqamah12/iqamah_period`; bounds carry `time12/period`; `gregorian_long == "Monday 20 October 2025"`-style for the pinned date; `countdown_label/target` matrix (NORMAL dhuhr → `Dhuhr call to prayer in` + adhan_iso; IQAMAH_COUNTDOWN → `Iqomah in` + iqamah_iso; ADHAN → `Iqomah in` + iqamah_iso; SALAH_DIM → `""`). Run: `uv run pytest tests/unit/test_display_context.py -q` → Expected: FAIL (missing keys).
- [ ] Implement: module-level `twelve_h(hhmm: str) -> tuple[str, str]`; extend card/bound dicts; `gregorian_long` via `day.date.strftime("%A %-d %B %Y")` (Linux-only `%-d` acceptable? repo runs Linux-only — verify no Windows concern in repo; else manual int strip); label/target branching on `event.state` mirroring `pre_note` precedent; server `clock` fallback text becomes 12h `H:MM` + period parts (keep `clock` key name; update existing clock test expectations only where the format changes). Verify → PASS.

### Task 2: clock_format default → 12h ripple + seeded migration

**Files:** `src/muhideen/core/values.py`, `api/fixtures/settings.json`, `src/muhideen/migrations/0004_theme_clock_default.sql` + `.down.sql` (create), tests pinning `24h-seconds` default, `src/muhideen/static/admin.js` (only if it hardcodes the default)

- [ ] Grep ripple first (done at plan time — see refs). Failing test: default `Settings().theme.clock_format == "12h"` + fixture parity + fresh-migrate seeds `12h`. Run → FAIL.
- [ ] Implement: `THEME_DEFAULTS["clock_format"] = "12h"`; fixtures to `12h`; migration `0004`: `UPDATE settings SET value='12h' WHERE key='theme.clock_format' AND value='24h-seconds'` (down reverses; pre-1.0 reinterpretation acceptable — explicit choices flipped too, noted in CHANGELOG); update `test_migrate.py` seeded expectations + add 0004 test (seeded-at-old-default → 12h; explicit `24h` untouched); update default-pinning tests (`test_core_values.py:529,620,637`, fixture test, `test_display.py:271` data attr, template-token + js-clock-format tests rewritten to new hooks in Task 6). Screen JS/template ignore the knob by design (design-locked 12h) — `data-clock-format` attr dropped; token test updated. Verify: `uv run pytest tests/unit tests/contract -q` → PASS.

### Task 3: Template rewrite (mockup adaptation)

**Files:** `src/muhideen/views/templates/display.html`

- [ ] Rewrite to mockup structure with bindings (body carries `{{ body_class }}` plus `state-{{ state|lower }}` for the dim treatment; header row static; `{% for c in cards %}` rows (`id="row-{{ c.key }}"`, `.current` when `is_next`, `{{ c.en }}`, `{{ c.time12 }}<small class="period">{{ c.period }}</small>`, iqamah likewise with `id="iqamah-{{ c.key }}"`); bounds strip (`id="bounds"`, `bound-{{ b.key }}` spans, gated by `show_boundaries`); clock block (`id="live-clock"`, server fallback `clock` parts with `.colon` span); dates (`gregorian_long`, `hijri_long`); mosque block (logo SVG verbatim, `masjid_name`, `zone`); countdown block only `{% if countdown_target %}` (`countdown-label` + `countdown` with `data-target`); hidden `#hero-clock` data span (all attrs); banner strip only `{% if banners %}`; audio tag only ADHAN+url; SALAH_DIM same layout + dim class + `data-dim-until` holder. No new backend keys beyond Task 1.
- [ ] Verify by render: `curl /display` contains all hooks; existing e2e will fail (expected — Task 6).

### Task 4: CSS rewrite (mockup stylesheet)

**Files:** `src/muhideen/static/app.css`

- [ ] Port the user-supplied stylesheet verbatim as the base (green tokens, grid, rows, strip, clock/date/mosque/countdown blocks, both media queries), then adapt: add `.colon` blink keyframes, `.prayer-row.current` exists already (keep), SALAH_DIM dimming (`body.state-dim .prayer-screen { filter: brightness(.07); }` — body needs the state class: add `state-{{ state|lower }}` alongside `body_class` in template), minimal `#banners` strip, `#hero-clock[hidden]`, keep `.slate-error`; drop webfont `@font-face` + theme-variant + old hero/card rules (dead after rewrite).
- [ ] Verify: Playwright screenshot at 1920×1080 + 9:16-ish viewport vs mockup (eyeball).

### Task 5: JS rewrite (clock/blink/countdown, retained skeleton)

**Files:** `src/muhideen/static/app.js`

- [ ] Keep: anchor/serverEpoch, SSE state/tick/config handlers, poll fallback, heartbeat, dim-skip (retargeted to `[data-dim-until]` holder), audio volume+play. Replace: clock renderer (12h HH:MM from server epoch + tz attr, `.colon` span static in DOM — JS updates only `hm` text + period), countdown renderer (single `[data-target]` → `HH:MM:SS` always with leading hours), delete bar/HMS/box code. `node --check` + curl-rendered smoke (countdown text matches `00:..` shape after 1s — assert via Playwright text read).
- [ ] Verify: `node --check` on the rewritten file; display JS has no references to removed ids (grep `getElementById` vs template ids); `admin.js` untouched.

### Task 6: Test updates for new structure

**Files:** `tests/e2e/test_display.py`, `tests/unit/test_display_context.py` (Task 1 covers unit additions; here fix/churn existing)

- [ ] Rewrite region assertions to new hooks (table rows, current class, 12h strings, countdown label/target attrs, blank countdown in SALAH_DIM, tz data attrs, iqamah elements, banner-strip conditional, trilingual-hero/card assertions replaced — backend still emits bm/ar in ctx, template no longer renders them: assert absence? No — assert new structure only, plus one test pinning ctx still carries bm/ar (backend-unchanged guard in unit file)). Template-token test token list updated to new hooks (`data-clock-format` dropped by design); `test_js_honours_clock_format` rewritten (JS is design-locked 12h; pin tz-attr handling instead).
- [ ] Verify: `uv run pytest tests/e2e/test_display.py tests/unit/test_display_context.py -q` → PASS.

### Task 7: Visual verification + full gate + cleanup

- [ ] Playwright screenshots (desktop 1920×1080 + mobile 390×844) of `/display` on the demo server; eyeball vs `new_screen.png` (layout, highlight, strip, clock, dates, countdown); fix glaring deltas.
- [ ] Delete `new_screen.png` from repo root (reference image must not ship).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: user bullets → Tasks 1–5 (countdown wording/format, 12h+blink, blank-when-idle, no-stage, 12h default); preserved list → template/JS hooks; dropped-render list explicit with backend-unchanged guard test.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task; files, ids, commands named.
3. Type consistency: `twelve_h` returns `tuple[str, str]`; ctx keys mirror existing naming (`time12/period`, `countdown_label/target`); `CycleMode`-style Literal untouched.
4. Momus dry gate: all file/line refs verified on disk this session; order fixed (builder → default → template/css/js → tests → visual); each task red→green with exact commands. `setup.html`/`admin.js`/engine untouched.
