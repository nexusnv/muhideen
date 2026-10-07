# Failure visibility + budget note Implementation Plan (issues #63, #86, #72)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Unexpected errors wake streams as `stage="error"` instead of killing them (#63); no-coords timetable failure gets actionable slate copy + docs (#86); open-window repeat equivalence documented and pinned (#72).

**Architecture:** Engine tick arm + API stream arm (adapters/api, no domain change); display-route slate copy (api/views); one docstring + one pin test (domain/tests); one deployment section. No contract, status-code, or playlist-math changes.

**Tech Stack:** Python 3.11+, uv, pytest (unit/e2e), ruff, strict pyright, import-linter.

**References:** Issues #63, #86, #72; `src/muhideen/engine/engine.py:117-145` (`tick`); `src/muhideen/api/app.py:344-367` (stage mapping), `:788-794` (ScheduleError slate); `src/muhideen/domain/stage.py:91-108` (`resolve_stage`); `tests/unit/test_domain_stage.py:381-392` (max_cycles style).

**Branch:** `fix/failure-visibility-63-86-72` (from `main`)

---

## File Structure

- Modify: `src/muhideen/engine/engine.py` — unexpected-error arm in `tick()`.
- Modify: `src/muhideen/api/app.py` — `except Exception` stage arm; no-coords slate copy.
- Modify: `src/muhideen/domain/stage.py` — one docstring sentence.
- Test: engine tick tests (grep for existing `tick()` tests first), stream tests, display route tests, `tests/unit/test_domain_stage.py`.
- Docs: `docs/deployment.md` — no-coords failure section.

---

### Task 1: Unexpected-error signal (#63)

**Files:** `src/muhideen/engine/engine.py`, `src/muhideen/api/app.py`, tick/stream test files (locate via `rg -ln "\.tick\(|_tick_stage|stage.*error" tests/` first)

- [ ] Add failing tests: (a) tick unit test — `next_event` raising `RuntimeError` → bare `TICK_EVENT` published on the bus + exception propagates; (b) stream test — unexpected error in stage resolution yields a `stage="error"` payload and the stream continues. Run each → Expected: FAIL (no signal today; stream dies).
- [ ] Implement minimal change: `tick()` gains the unexpected-error arm (publish + `logger.exception` + re-raise, mirroring the `MuhideenError` arm); stage mapping gains `except Exception: logger.exception(...); stage = "error"` after the `MuhideenError` arm. Check `ruff check` accepts the `except Exception` (log-and-degrade is the codebase idiom per `_run_ticker`).
- [ ] Verify: new tests PASS; neighboring suites (`-k "tick or stage or stream or events"`) PASS.

### Task 2: No-coords guidance slate + docs (#86)

**Files:** `src/muhideen/api/app.py` (`:788-794`), display route tests, `docs/deployment.md`

- [ ] Add failing test: no-coords settings (`lat`/`lon` None) + empty timetable → `GET /display` 404 HTML contains the coordinates-or-sync guidance copy; coords-present missing-timetable case keeps the bare slate. Run → Expected: FAIL (bare "No schedule" today).
- [ ] Implement: in the `except ScheduleError` arm, choose the message by `settings.lat is None and settings.lon is None`. Status stays 404. Verify: new test PASS; existing display tests PASS.
- [ ] Docs: short `docs/deployment.md` subsection (same failure, operator remedy). Verify: `rg -n "coordinates" docs/deployment.md` hits the new section.

### Task 3: Open-window equivalence note + pin (#72)

**Files:** `src/muhideen/domain/stage.py` (docstring), `tests/unit/test_domain_stage.py`

- [ ] Docstring: append to the cycle-budget parenthetical — open-window (no start bound) `repeat` never exhausts and is equivalent to `indefinite`, by intent.
- [ ] Pin test mirroring `:381-392` style: `_playlist("open", None, None, cycle_mode="repeat", max_cycles=1)` still occupies at a `now` far past one full item-set loop. Run → Expected: PASS throughout (current-behavior guard).

### Task 4: Full gate and PR

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → all green (format branch files in a `style:` commit if flagged; BLOCKED on anything outside branch files).
- [ ] Review: `git log --oneline main..HEAD && git diff main --stat` → only Task 1–3 files + spec/plan docs.
- [ ] Push, open PR titled `fix(visibility): unexpected-error signal + no-coords guidance (#63, #86, #72)`, body cites the three recorded decisions + gate evidence, `Closes #63, #86, #72`.
