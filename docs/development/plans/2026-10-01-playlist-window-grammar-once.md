# Parse the Playlist window grammar once — Implementation Plan (issue #30)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `paxman-momus-review` (plan), `paxman-oracle-review` (after impl).

**Goal:** Own the Playlist window grammar (`HH:MM` or marker name plus offsets, `None` stays open, inverted bounds span midnight) in one domain seam so write-time and resolve-time agree and a DTO-accepted string can never fail only at Stage resolve.

**Architecture:** New `domain/playlist_window.py` owns parsing (`WindowBound` typed) plus window membership; `api/app.py` `PlaylistDTO.to_domain` and `adapters/playlist_repo.py` load both call it (write and read seams reject once, boundary anchors rejected once); `domain/stage.py` deletes its private string parser and resolves typed windows only; `_occupancy_preview` samples through the same one-moment tick helper as `_tick_stage`. No schema migration, no wire change, no new dependency. `Playlist` stays the raw carrier (TEXT columns unchanged); the typed form is the resolve-time view.

**Tech Stack:** Python 3.11+, uv, ruff (88 cols), strict pyright on `src/`, import-linter layers `api → engine → adapters → domain → core`, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #30; `src/muhideen/core/values.py:325-354` (Playlist raw strings); `src/muhideen/api/app.py:150-236` (`_check_window_bound`, `PlaylistDTO`); `src/muhideen/api/app.py:326-336` (`_tick_stage`); `src/muhideen/api/app.py:989-1041` (`_occupancy_preview` 288-sample loop); `src/muhideen/domain/stage.py:68-124` (`_parse_bound`, `_playlist_window`, `_in_window`); `src/muhideen/domain/stage.py:126-171` (`resolve_stage`); `src/muhideen/adapters/playlist_repo.py:22-62` (verbatim round-trip); `ARCHITECTURE.md:63-77` (layers flow inward); `CONTEXT.md:54-62` (Main Stage, Playlist, Countdown); `CONTRIBUTING.md:38-51` (quality gate); `TESTING_STRATEGY.md`.

**Branch:** `feature/playlist-window-grammar`

---

## File Structure

- Create: `src/muhideen/domain/playlist_window.py` — `WindowBound`, `TypedWindow`, `parse_bound`, `parse_window`, `window_datetimes`, `is_in_window`
- Modify: `src/muhideen/domain/stage.py` — delete `_parse_bound`, delegate to `playlist_window`; keep `resolve_stage` signature
- Modify: `src/muhideen/api/app.py` — delete `_check_window_bound` + `_HHMM_RE`; `PlaylistDTO.to_domain` calls `parse_window`; extract `_preview_moment` shared by `_tick_stage` and `_occupancy_preview`
- Modify: `src/muhideen/adapters/playlist_repo.py` — `_playlist_from_rows` validates window bounds via `parse_window` (maps to `ConfigError`)
- Test: `tests/unit/test_domain_playlist_window.py` (create) — grammar matrix
- Test: `tests/unit/test_domain_stage.py` — existing matrix must stay green; add typed-delegation guard
- Test: `tests/e2e/test_admin_playlists.py` — write-time 422s plus preview parity
- Test: `tests/integration/test_playlist_repo.py` — corrupt window row raises `ConfigError`

No migration file; no `docs/api-contract.md` change (wire unchanged); no `api/fixtures/` change.

---

### Task 1: Typed window seam with grammar matrix

**Files:** `src/muhideen/domain/playlist_window.py` (create), `tests/unit/test_domain_playlist_window.py` (create)

**Goal:** One parser owns `HH:MM` vs marker-name plus boundary-anchor rejection and midnight membership.

- [ ] Failing test first: create `tests/unit/test_domain_playlist_window.py` with `test_parse_clock_bound`, `test_parse_marker_bound_case_insensitive_and_stripped`, `test_parse_none_stays_open`, `test_parse_rejects_unknown_and_boundary_anchor`, `test_inverted_spans_midnight`, `test_anchor_mode_ignores_clock_bounds`. Run: `uv run pytest tests/unit/test_domain_playlist_window.py -v` → Expected: FAIL (`ModuleNotFoundError: playlist_window`).
- [ ] Implement minimal seam: frozen `WindowBound` (`kind: Literal["clock","marker"]`, `clock: time | None`, `marker: MarkerName | None`), frozen `TypedWindow` (`start`, `end`, `anchor`, `start_offset`, `stop_offset`), `parse_bound(raw: str | None) -> WindowBound | None` (strip + lower, `HH:MM` via `time.fromisoformat` guarded to `00:00-23:59`, else `MarkerName`, else `ValueError`; boundary markers allowed as clock-bound markers here — anchor rejection lives in `parse_window`), `parse_window(playlist: Playlist) -> TypedWindow` (rejects boundary `anchor_marker` with `ValueError`, rejects boundary-vs-clock mixing only where today rejects: boundary anchor rejected, clock bounds may name boundary markers as today does), `window_datetimes(typed, day, now) -> tuple[datetime|None, datetime|None]`, `is_in_window(start, stop, now) -> bool` (half-open `[start,stop)`, inverted spans midnight). Pure, no I/O, no wall-clock read. Verify: `uv run pytest tests/unit/test_domain_playlist_window.py -q` → PASS; `uv run ruff check src/muhideen/domain/playlist_window.py && uv run pyright src/muhideen/domain/playlist_window.py` → green.

### Task 2: Write and read seams reject once

**Files:** `src/muhideen/api/app.py:150-236`, `src/muhideen/adapters/playlist_repo.py:44-62`

**Goal:** DTO and repo load call the same parser; `_check_window_bound` deleted.

- [ ] Failing test: add `test_window_marker_with_offset_round_trip` to `tests/e2e/test_admin_playlists.py` (POST `window_start="asr"` with `anchor_start_offset_min=25` then GET equals) and `test_corrupt_window_bound_raises_config_error` to `tests/integration/test_playlist_repo.py` (UPDATE `playlists SET window_start='bogus'` then `repo.get` raises `ConfigError`). Run: `uv run pytest tests/integration/test_playlist_repo.py -k corrupt_window -v` → Expected: FAIL (no validation, returns raw `bogus`).
- [ ] Implement: delete `_HHMM_RE` and `_check_window_bound` from `src/muhideen/api/app.py`; `PlaylistDTO.to_domain` builds the `Playlist` then calls `parse_window` (maps `ValueError` to `ValueError` with field context, routes already map to 422); `_playlist_from_rows` in `src/muhideen/adapters/playlist_repo.py` calls `parse_window` after construction and maps `ValueError` to `ConfigError`. Keep `anchor_marker` boundary rejection inside `parse_window` only. Verify: `uv run pytest tests/e2e/test_admin_playlists.py -k "validation or marker_anchored or corrupt" -q` → PASS; `uv run pytest tests/integration/test_playlist_repo.py -q` → PASS. Depends on: Task 1.

### Task 3: Stage resolves typed windows only

**Files:** `src/muhideen/domain/stage.py:68-124`

**Goal:** `resolve_stage` never touches raw strings; string grammar lives only in Task 1.

- [ ] Failing test: add `test_stage Shares parser with write seam` to `tests/unit/test_domain_stage.py` asserting `muhideen.domain.stage._parse_bound` does not exist (`assert not hasattr(stage, "_parse_bound")`) and `test_invalid_window_bound_raises` still raises `ConfigError` for `window_start="bogus"` via the shared seam. Run: `uv run pytest tests/unit/test_domain_stage.py -k "invalid_window or shares_parser" -v` → Expected: FAIL (`_parse_bound` still exists).
- [ ] Implement: delete `_parse_bound` from `src/muhideen/domain/stage.py`; `_playlist_window` calls `parse_window` then `window_datetimes`; `_in_window` delegates to `is_in_window`; `resolve_stage` keeps its signature `(now, day, settings, event, playlists)` and its countdown/activation/max-cycles logic unchanged, mapping `ValueError` from `parse_window` to `ConfigError`. No behavior change for valid windows (existing matrix pins it). Verify: `uv run pytest tests/unit/test_domain_stage.py -q` → PASS. Depends on: Task 1, Task 2.

### Task 4: Preview shares the tick seam + full gate

**Files:** `src/muhideen/api/app.py:326-336`, `src/muhideen/api/app.py:989-1041`

**Goal:** `_occupancy_preview` and `_tick_stage` resolve one moment through the same helper; no drift between live tick and 24h preview.

- [ ] Failing test: add `test_preview_moment_matches_tick_stage` to `tests/e2e/test_admin_playlists.py` (pin `surface.clock`, create open-window playlist, assert `GET /api/playlists/preview` `stage` equals `stage_id(resolve_stage(...))` at same `now`, and `next_at` for `23:00-23:30` future playlist contains `23:00`). Run: `uv run pytest tests/e2e/test_admin_playlists.py -k preview -v` → Expected: PASS already (characterization); the red is structural: assert `app._preview_moment` exists → FAIL.
- [ ] Implement: extract `_preview_moment(engine, settings, playlists, moment) -> StageOccupant` (loads day via `engine.resolve_day`, event via `engine.next_event`, calls `resolve_stage`); `_tick_stage` calls it at `event.now`; `_occupancy_preview` loops 288×5min calling it with a per-date day cache (keep cache, share resolve). Delete duplicated `resolve_stage`+`engine.next_event` inline block. Verify: `uv run pytest tests/e2e/test_admin_playlists.py -q` → PASS.
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `! rg -n "fastapi|httpx|datetime\\.now|time\\.time" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/` → 0 hits; `! rg -n "sqlite3" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/ tests/unit/` → 0 hits; `uv run pytest -q` → green; `rg -n "_check_window_bound|_HHMM_RE|_parse_bound" src/muhideen/` → 0 hits (both private parsers gone).
