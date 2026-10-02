# Product surface lock-in: carousel, cycle modes, image-only, override scope (issue #45)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: self-review below (Momus unavailable this session), `oracle-review` on the branch diff before PR handoff.

**Goal:** Every v1.0 product switch is either wired end-to-end or explicitly locked in: group carousel flag drives the display, playlists gain a real `repeat` cycle mode, image-only and display-override scope are documented as intentional — API, docs, and UI agree with tests pinning each.

**Architecture:** `Playlist.cycle_mode` grows `"repeat"` (requires `max_cycles>=1`; `"indefinite"` requires `max_cycles=None`, enforced in the VO so DTO/repo/API inherit); `resolve_stage` budgets `repeat` by `max_cycles` and loops `indefinite` unbounded; the display route resolves the display's group `carousel_enabled` (default on) into a `show_carousel` context flag hiding `#carousel-dot`; contract + PRD record image-only items and theme+dim-only overrides as deliberate v1.0 scope. Recorded user decisions: wire carousel, real cycle modes, stay image-only.

**Tech Stack:** Python 3.11+, pure domain (`stage.py`), FastAPI/Pydantic, SQLite, Jinja + vanilla JS, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #45 (decisions recorded this session); `src/muhideen/domain/stage.py:115-136` (winner loop + max_cycles budget); `src/muhideen/core/values.py:465-466` + `src/muhideen/api/app.py:204-205` (`cycle_mode` Literals); `src/muhideen/static/admin.js:531-532,546` (cycles input, hardcoded indefinite); `src/muhideen/views/templates/display.html:90` (`carousel-dot`, footer already NORMAL-only = FR-3.3 pause); `src/muhideen/api/app.py:1060-1067` (group read incl. carousel flag), `app.py:658-682` (display route override resolution); `src/muhideen/adapters/sqlite_repo.py` + `playlist_repo.py` (playlist persistence — grep `cycle_mode` for validation sites); `api/fixtures/playlist.json` (cycle fields); `docs/api-contract.md` (playlist + display-group sections); PRD FR-3.1/3.2/4.2/4.3.

**Branch:** `feature/product-lockin` (cut when approved; base on `main`)

---

## Background the implementer needs

### Locked decisions (user, this session)

- Carousel: WIRE (not remove/reserve). Semantics per PRD FR-3.2/3.3: per-group (else default-on) toggle for the carousel indicator; footer already renders NORMAL-only so the pause rule holds structurally — the flag only gates visibility.
- Cycle modes: REAL modes, image-only kept. `indefinite` (default, loops forever, `max_cycles` must be None) + `repeat` (requires `max_cycles>=1`, releases after N full passes). `once` is NOT added (`repeat:1` covers it — no redundant enum).
- Image-only: DOCUMENT (no video/audio items in v1.0; FR-3.1 already says so — make the contract explicit).
- Override scope: DOCUMENT as intentional per FR-4.3 (theme.* + dim only; schedule/iqamah never fork per display).

### Design details (locked)

1. VO validation (`Playlist.__post_init__`, mirroring `PlaylistItem` guard style): `repeat` without `max_cycles` → ValueError; `indefinite` with non-None `max_cycles` → ValueError. DTO Literals widen in both `values.py` and `app.py` (`PlaylistDTO`); repo load path inherits via constructor (check `playlist_repo.py` construction site — no separate validation needed if it builds `Playlist(...)`).
2. Stage: `indefinite` → skip the budget check entirely; `repeat` → current max_cycles logic (unchanged math). Existing `indefinite`+null rows behave identically; any tests/fixtures pinning `indefinite`+set-max_cycles must be updated to the legal combination (grep `max_cycles` in `tests/` + `api/fixtures/` first — compat break is pre-1.0 and intended).
3. Carousel: display route already loads group overrides per display id (mirroring `group_dim_override`); add `show_carousel = group row carousel_enabled if known else True` (unknown ids render global default = on, mirroring the theme fallback); builder or direct ctx key? The display builder owns presentation flags (`show_boundaries` precedent in `build_display_context`) — thread it the same way if the builder takes settings; else set `ctx["show_carousel"]` in the route next to `dim_minutes`/`dim_source` (read the route first, follow the dim precedent exactly). Template: `{% if show_carousel %}` around `#carousel-dot`. Unknown-id and global-theme tests must stay green.
4. Admin playlist editor: cycles input maps to mode (blank → `indefinite`+null; N → `repeat`+N) instead of hardcoded `indefinite`; add a mode indicator or keep the number-only UX with a hint (read the playlists editor block first — minimal DOM change, hint text preferred).
5. Docs: contract playlist section (modes + image-only explicit), display-groups section (carousel flag effect + override-scope statement), PRD scoping notes (FR-3.1 image-only reaffirmed, FR-4.2/4.3 override scope intentional). CHANGELOG Added entry.
6. Non-goals: `once` mode, video/audio items, per-display schedule fork, global carousel settings flag (groups only, per FR-4.2), `locales` changes.

## File Structure

- Modify: `src/muhideen/core/values.py` — `CycleMode` Literal + `Playlist` validation
- Modify: `src/muhideen/domain/stage.py` — mode-branched budget (indefinite unbounded)
- Modify: `src/muhideen/api/app.py` — `PlaylistDTO.cycle_mode` Literal + display-route `show_carousel`
- Modify: `src/muhideen/views/display.py` — `show_carousel` ctx flag (only if builder owns flags; else route-level)
- Modify: `src/muhideen/views/templates/display.html` — gate `#carousel-dot`
- Modify: `src/muhideen/static/admin.js` — cycles input → mode mapping
- Test: stage unit tests (find via `grep -rln "resolve_stage\|max_cycles" tests/`), DTO/repo/API tests touching cycle fields, display e2e (dot shown by default + hidden on group toggle), admin save test
- Fixtures: `api/fixtures/playlist.json` (+ create fixture if cycle fields live there) — update only if they pin the illegal combo
- Docs: `docs/api-contract.md`, `PRD.md` scoping notes, `CHANGELOG.md`

No migration (no schema change — mode/max_cycles already columns), no new routes, no locales change.

---

### Task 1: Repeat cycle mode end-to-end

**Files:** `values.py`, `stage.py`, `app.py` DTO, repo construction site, fixtures, cycle tests, admin.js playlist save

- [ ] Failing tests: VO `Playlist(cycle_mode="repeat", max_cycles=None)` → ValueError; `Playlist(cycle_mode="indefinite", max_cycles=3)` → ValueError; stage `repeat:2` releases after 2 passes (mirror the existing max_cycles stage test with mode set); API PUT playlist `{cycle_mode:"repeat", max_cycles:2}` → 200 + GET pins; `{cycle_mode:"repeat"}` without max → 422. Find existing cycle tests first (`grep -rn "max_cycles" tests/ | head -20`) and update illegal-combo fixtures to legal ones. Run: selected → Expected: FAIL (`ValueError` unraised / Literal reject).
- [ ] Implement: Literal widening (both VO + DTO — check for a shared `CycleMode` alias opportunity; DTO currently inlines `Literal["indefinite"]` at `app.py:204`, VO at `values.py:465`); VO `__post_init__` rules; stage branch (`indefinite` skips budget; `repeat` keeps current math); repo inherits (verify construction); admin.js maps cycles input → mode (`""` → indefinite/null; N → repeat/N) + hint text; fixtures updated only if illegal. Verify: cycle tests + `tests/contract -q` → PASS (fixtures parity!), full unit+integration → PASS.
- [ ] Lint touched files (`ruff` + `lint-imports` — Literal lives in values, imported by app already).

### Task 2: Carousel flag drives the display

**Files:** display route (`app.py` group-override area), `display.py` or route ctx, `display.html:90`, display e2e

- [ ] Failing tests: default render contains `#carousel-dot`; with group override `carousel_enabled=0` for the display's group (reuse the group-dim test pattern — find via `grep -rn "group_dim_override\|display_groups" tests/e2e/test_display.py`) → dot absent, everything else identical; unknown display id → dot present. Run → Expected: FAIL (dot always present).
- [ ] Implement: resolve group flag next to `group_dim_override` (same query path); `show_carousel` ctx (builder flag mirroring `show_boundaries`, or route-level mirroring dim — read first, follow exactly); template `{% if show_carousel %}` around the dot span only. Verify: e2e file → PASS; full e2e → PASS.
- [ ] Lint touched files.

### Task 3: Lock-in docs + changelog + full gate

- [ ] Contract: playlist section (modes table + image-only explicit incl. upload allowlist pointer), display-groups section (carousel effect + theme+dim-only scope statement). PRD: FR-3.1 reaffirm image-only for v1.0; FR-4.2/4.3 note override scope intentional. CHANGELOG `[Unreleased] Added` (or Changed — new enum value is additive; carousel wiring is a behavior fix; pick per entry, no dev-plan filename ref).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #45 four switches → Tasks 1 (cycle modes wired), 2 (carousel wired), 3 (image-only + override scope documented). Wire/remove/reserve recorded per flag; `once`/video/per-display-schedule/global-flag excluded with reasons.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task; grep-first steps named.
3. Type consistency: `Literal["indefinite","repeat"]` mirrored VO↔DTO; `max_cycles: int|None` rules enforced once (VO); `show_carousel: bool` default True; fixture JSON reflects legal combos.
4. Momus dry gate: `stage.py:115-136` / `values.py:465-466` / `app.py:204-205,658-682,1060-1067` / `display.html:90` / `admin.js:531-546` verified on disk; order fixed (domain+wire → render → docs); each task red→green. Engine/scheduler/locales untouched; no migration.
