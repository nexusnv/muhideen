# Phase 1C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the display to the approved example, add the Main Stage engine with Playlists, and modernize admin — vanilla stack, offline-first intact.

**Architecture:** Presentation and behavior stay separated: pure domain `stage.py` resolves occupancy over (now, schedule, settings, playlists); the builder renders per-state variants; vanilla JS only ticks and reloads (now also on Stage change via additive `tick.stage`). Playlists persist in two new tables; images reuse the retired-2A Pillow pipeline. Theme knobs are settings keys under `theme.*`; per-display overrides live in `display_settings` with an enforced allowlist.

**Tech Stack:** Jinja2, vanilla CSS/JS, Pillow (in deps), hijridate (in deps), vendored OFL woff2, pytest e2e, Playwright screenshots (manual acceptance gate).

---

## Global invariants (all tasks)

- No Tailwind, no CDN, no Google Fonts link. Offline-first is non-negotiable.
- `views/` imports `api.dto` + `core` only (forbidden contract enforced).
- Contract changes are additive only (`tick.stage`, never renames).
- TDD red→green, frequent commits, full gate per slice (`ruff check . && ruff format --check . && pyright && lint-imports && pytest -q`, coverage ≥95).

---

### Task 1: Vendor fonts

**Files:**
- Create: `src/muhideen/static/fonts/Outfit-<weights>.woff2`, `src/muhideen/static/fonts/JetBrainsMono-<weights>.woff2`
- Modify: `src/muhideen/static/app.css` (`@font-face` block)

- [ ] **Step 1: Download OFL woff2 (latin subsets)**

```bash
mkdir -p src/muhideen/static/fonts && cd src/muhideen/static/fonts
for w in 400 700 800; do curl -sL -o "Outfit-$w.woff2" "https://fonts.gstatic.com/s/outfit/v11/QGYyz_MVcBeNP4NjuGObqx1XmOowAwig9lB878e-GA.ttf" || true; done
```

The exact gstatic URLs vary — fetch the real ones from the Google Fonts CSS API (`curl -s "https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;800&display=swap" -A "Mozilla/5.0"` lists woff2 URLs), Outfit 400/700/800 + JetBrains Mono 600/800, latin subset only. Verify each file starts with `wOF2` magic (`head -c 4 file | od -An -tx1`) and record licenses (both OFL — note in commit message).

- [ ] **Step 2: Fallback rule**

If download is impossible, skip vendoring and use the system stack everywhere (`system-ui`, `ui-monospace`); record the fallback in the commit message. Do not block the slice on fonts.

- [ ] **Step 3: `@font-face` + commit**

```css
@font-face { font-family: "Outfit"; src: url("fonts/Outfit-400.woff2") format("woff2"); font-weight: 400; font-display: swap; }
/* ... per weight + JetBrains Mono ... */
body { font-family: "Outfit", system-ui, sans-serif; }
.digital-font { font-family: "JetBrains Mono", ui-monospace, monospace; }
```

```bash
git add src/muhideen/static/fonts/ src/muhideen/static/app.css
git commit -m "feat: vendor display fonts (OFL)"
```

---

### Task 2: Per-card iqamah + Hijri long format

**Files:**
- Modify: `src/muhideen/views/display.py`, `tests/unit/test_display_context.py`, `tests/unit/test_display_states.py`

- [ ] **Step 1: Failing tests**

```python
def test_cards_carry_iqamah_times():
    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )
    by_key = {c["key"]: c for c in ctx["cards"]}
    assert by_key["fajr"]["iqamah"] == "06:00"  # adhan + rule delay
    assert by_key["dhuhr"]["iqamah"] == "12:25"


def test_hijri_long_format():
    ctx = build_display_context(
        day=_day(), event=_event("NORMAL"), settings=_settings()
    )
    assert ctx["hijri_long"] == "28 Rabi' al-Awwal 1447"
```

(Derive expected iqamah values from `domain/iqamah.py::resolve_iqamah` + the test rules — read that file first; adjust the literals to the true computed values before asserting. The Hijri literal follows from `hijridate` for 2025-10-20 — verify with `resolve_hijri`.)

- [ ] **Step 2: Run red** — `uv run pytest tests/unit/test_display_context.py -q`, expect FAIL (missing keys).
- [ ] **Step 3: Implement** — builder: for each of the 5 prayers combine `day.date` + card time with `event.now.tzinfo`, call `resolve_iqamah(prayer, adhan_at, rules)` where `rules = {r.prayer: r for r in settings.iqamah_rules}` (Friday: dhuhr card uses the Jumuah rule + Jumuah labels, matching existing `is_next` logic); format `%H:%M` into `card["iqamah"]`. Hijri: `HIJRI_MONTHS = ("Muharram", "Safar", "Rabi' al-Awwal", "Rabi' al-Thani", "Jumada al-Ula", "Jumada al-Akhirah", "Rajab", "Sha'ban", "Ramadan", "Shawwal", "Dhuʻl-Qa'dah", "Dhuʻl-Hijjah")`, parse `day.hijri_date` (`YYYY-MM-DD`) → `f"{d} {name} {y}"`, `None`/unparseable → fall back to the raw wire value.
- [ ] **Step 4: Green** — tests pass, ruff/format/pyright clean.
- [ ] **Step 5: Commit** — `git commit -m "feat: per-card iqamah and Hijri long format"` with the touched files.

---

### Task 3: Display reskin to approved example

**Files:**
- Modify: `src/muhideen/views/templates/display.html`, `src/muhideen/static/app.css`, `src/muhideen/static/app.js`, `tests/unit/test_display_static.py`, `tests/e2e/test_display.py`

- [ ] **Step 1: Failing tests** — extend static tests: tokens `countdown-box`, `iqamah-row`, `brand-block`, `glow-emerald` (names match the new CSS classes below); e2e: `#iqamah-fajr` style per-card iqamah element present with `HH:MM`.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — rewrite hero/cards/footer markup and CSS to the approved example structure (gradient hero card, H/M/S countdown boxes fed by existing `data-countdown`, giant iqamah view, five cards each with name + time + iqamah row, footer with brand block + live time + both dates + boundary strip). Keep every existing id/data-attr contract (`hero-clock`, `card-*`, `bound-*`, `data-now/state/next/adhan/tz*`, `bound-next`, `slate`, `dim*`, `overlay-adhan`) — JS behavior unchanged. Static `screen_inspiration_1.png` (committed at `docs/development/specs/`) is the visual reference.
- [ ] **Step 4: Green + Playwright gate** — unit/e2e pass; then `playwright-cli` screenshot at 1920×1080 vs the inspiration image (manual eyeball gate, attach both to the PR; mismatches in density/hierarchy fail the task).
- [ ] **Step 5: Commit** — `git commit -m "feat: display reskin to approved example"`.

---

### Task 4: Countdown settings (Settings VO → repo → DTO)

**Files:**
- Modify: `src/muhideen/core/values.py`, `src/muhideen/adapters/sqlite_repo.py`, `src/muhideen/migrations/0001_initial.sql` (seeds only), `src/muhideen/api/dto.py`, `api/fixtures/settings.json`, `docs/api-contract.md`, `tests/unit/test_core_values.py`, `tests/integration/test_sqlite_settings_repo.py`
- Test: same test files

- [ ] **Step 1: Failing tests** — `Settings(..., countdown_before_adhan_min=5)` defaults 5; guards reject -1/91; per-prayer overrides dict round-trips.
- [ ] **Step 2: Run red** — unexpected keyword argument.
- [ ] **Step 3: Implement** — `Settings` gains `countdown_before_adhan_min: int = 5` (guard 0–90) + `countdown_before_adhan_overrides: dict` — store as `Mapping[str, int]` keyed by prayer value; repo persists `countdown_min_default` + `countdown_min_<prayer>` keys (absent = default); DTO `countdown_before_adhan_min: int (0–90)` + `countdown_before_adhan_overrides: dict[str, int]`; fixture + contract example; effective rule helper `countdown_window(settings, prayer) -> int` in `domain/` (override wins, else default).
- [ ] **Step 4: Green + gate files clean.**
- [ ] **Step 5: Commit** — `git commit -m "feat: countdown takeover settings"`.

---

### Task 5: Stage engine (pure domain)

**Files:**
- Create: `src/muhideen/domain/stage.py`, `tests/unit/test_domain_stage.py`
- Modify: `src/muhideen/core/values.py` (Playlist/PlaylistItem value objects — data shapes only, no behavior)

Shapes (locked): `Playlist(id, title, active, window_start, window_end, anchor_marker, anchor_start_offset_min, anchor_stop_offset_min, cycle_mode, max_cycles, items)` where `cycle_mode ∈ {"indefinite"} + per-item durations on items; `PlaylistItem(image_path, duration_s, sort_order)`. Times as `HH:MM` strings or marker refs — mirror existing value-object conventions in the file.

`resolve_stage(now, day, settings, prayers_state, playlists) -> StageOccupant` with `StageOccupant ∈ {Clock} | {Countdown(kind)} | {Playlist(id)}`. Rules: countdown windows (pre-adhan via Task 4 helper; iqamah window = adhan overlay end → iqamah_at) outrank everything; else most-recently-activated in-window playlist; else Clock. Deterministic over pinned inputs.

- [ ] Steps 1–5 per standard (occupancy matrix tests: empty→Clock, single playlist, overlap→most-recent, countdown override both, marker-anchored window math). Commit `feat: main stage occupancy engine`.

---

### Task 6: Tick stage + client reload

**Files:**
- Modify: `src/muhideen/api/dto.py` (TickEventDTO gains `stage: str`), `src/muhideen/api/app.py` (ticker computes stage id), `src/muhideen/static/app.js` (reload when stage changes), `api/fixtures/events-stream.txt`, `docs/api-contract.md`, `tests/contract/*`, `tests/e2e/test_events_stream.py`, `tests/unit/test_display_static.py` (token `tickStage`/`data-stage`? match implementation)

Stage id string: `"clock"`, `"countdown:adhan:fajr"`, `"countdown:iqamah:dhuhr"`, `"playlist:<id>"`. Ticker resolves it alongside `next_event` (same pinned now). JS keeps `lastStage` from tick payloads; mismatch → reload. Additive contract field — fixtures + doc updated.

- [ ] Steps 1–5 per standard. Commit `feat: tick carries stage id`.

---

### Task 7: Playlist backend (store + CRUD)

**Files:**
- Create: `src/muhideen/adapters/playlist_repo.py` (or extend sqlite_repo — follow the existing repo pattern in-file), `tests/integration/test_playlist_repo.py`
- Modify: `src/muhideen/migrations/0002_playlists.sql` (new migration: `playlists` + `playlist_items` tables), `src/muhideen/media/*`? No — image bytes reuse the retired-2A Pillow pipeline: re-encode, EXIF strip, 5MB/50-item caps, JPG/PNG/WebP allowlist (copy the exact validation from the deleted 2A plan? It's gone with the branch — re-derive from PRD FR-3.1 + Pillow; keep it minimal: `adapters/images.py::store_image(data) -> path` raising ValueError).

`playlists(id TEXT PK, title, active 0/1, window_start, window_end, anchor_marker, anchor_start_offset_min, anchor_stop_offset_min, cycle_mode, max_cycles)`; `playlist_items(id, playlist_id FK, path, duration_s, sort_order)`.

- [ ] Steps 1–5 per standard (CRUD, ordering, active toggle, FK cascade, image validation incl. EXIF-strip proof via `getexif() == {}`). Commit `feat: playlist storage backend`.

---

### Task 8: Playlist editor UI + admin restyle

**Files:**
- Modify: `src/muhideen/views/templates/admin/*`, `src/muhideen/static/admin.css`, `src/muhideen/static/admin.js`, `src/muhideen/api/app.py` (playlist CRUD routes + occupancy preview data), `tests/e2e/test_admin_pages.py`, `tests/unit/test_admin_static.py`
- Test: same + new `tests/e2e/test_admin_playlists.py`

Admin nav regroup (locked): Profile (name, zone) · Time & Date Marker (method, lat/lon, calc-only, offsets, hijri, boundary countdown, pre-adhan windows, iqamah rules, adhan duration) · Display (theme knobs) · Playlists (list, editor, toggles) · System (QR, version). Same fields/endpoints; CSS restyle (small headings, consistent buttons, horizontal nav, multi-column forms). Playlist editor: schedule, cycling, items, occupancy preview ("on Stage now / next at …" computed server-side via Task 5). Per-display theme + dim overrides plumbed through the display registry (`display_settings` table — new in Task 7 migration if registry lacks it; check schema first).

- [ ] Steps 1–5 per standard, split into two commits if large (`feat: admin playlist editor`, `feat: admin restyle and per-display overrides`).

---

### Task 9: Theme knobs

**Files:**
- Modify: `src/muhideen/core/values.py` (ThemeSettings: palette, font, countdown_style, clock_format, hijri_form, boundary_strip, density + guards), `sqlite_repo` (theme.* keys), `dto.py`, `settings.json`, `api-contract.md`, templates/CSS (consume knobs), tests at each layer.

Knob values are closed enums (e.g. `countdown_style ∈ {"boxes","inline"}`, `clock_format ∈ {"24h","24h-seconds","12h"}`); invalid → 422 via existing guards. Builder maps knobs to template flags; CSS implements both variants for countdown style at minimum.

- [ ] **Step 6: `/display?id=` consumes stored theme/dim (render path)** — seed per-display theme/dim overrides, `GET /display?id=`, assert the stored knobs are applied in the rendered page (not defaults); e2e test pins it.

- [ ] Steps 1–5 per standard. Commit `feat: theme knobs`.

---

### Task 10: CHANGELOG + full gate

- [ ] CHANGELOG entries per slice; full gate green; coverage ≥95; `ruff format` the plan doc too. Commit `chore: 1C changelog`.

---

## Self-review

1. **Spec coverage:** every locked decision maps (vanilla/fonts→T1+reskin; iqamah→T2; Hijri long→T2; countdown settings→T4; Stage/overlap→T5–T6; upload/storage→T7; editor+restyle+overrides→T8; knobs→T9). Manual Playwright gate in T3; manual browser checklist stays owner-side per pre-flight.
2. **Placeholder scan:** exact paths/signatures/commands throughout; named fallbacks (font download, schema check) with verification steps.
3. **Type consistency:** `tick.stage` string ids; Playlist shapes mirror existing VO conventions; settings keys namespaced (`countdown_min_*`, `theme.*`); `display_settings` allowlist = theme.* + dim keys only.
