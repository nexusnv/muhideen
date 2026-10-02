# Failure visibility: surface config errors, retry by error class — Implementation Plan (issue #44)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Misconfiguration never renders as a healthy display: tick/preview surface error state instead of Clock, sync retries distinguish transient from unrecoverable failures, and the offline-first-no-coordinates outcome is a documented, tested slate.

**Architecture:** Add `transient: bool = True` to `SyncError` (client marks 4xx request-level rejections `transient=False`); `sync_job` skips retries for non-transient errors and continues transient failures on a 6h long-pole chain instead of going silent after 3 short retries; the SSE tick maps any `MuhideenError` to a new documented `error` stage (display reloads into its 503/404 slate) and the occupancy preview returns an `error` shape instead of silent `None`; the `_tomorrow`-never-from-last-known invariant is kept (deliberate) and documented; deployment docs record the offline-first-no-coordinates outcome.

**Tech Stack:** Python 3.11+, APScheduler, SSE, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #44; `src/muhideen/core/errors.py:30-37` (`SyncError`); `src/muhideen/adapters/jakim_esolat.py:241-287` (4xx fail-fast raising `SyncError` at `:276`); `src/muhideen/adapters/scheduler.py:40-43,129-183` (`RETRY_DELAYS_S`, `sync_job`); `src/muhideen/api/app.py:327-336` (`_tick_stage`), `app.py:409-427` (tick branch `except MuhideenError → "clock"`), `app.py:989-1033` (`_occupancy_preview` catching `(ConfigError, ScheduleError) → None`); `src/muhideen/engine/engine.py:210-217` (`_tomorrow` never last-known, deliberate); `src/muhideen/api/dto.py:233-243` (`TickEventDTO.stage: str`); `tests/integration/test_scheduler_sync.py:227-279` (chain + give-up tests pinning current behavior); `tests/integration/test_jakim_esolat.py:281-299` (4xx fail-fast tests); `tests/contract/test_events_stream.py:144` (tick sample); `docs/deployment.md` (offline-first/install docs).

**Branch:** `feature/failure-visibility` (cut when approved; base on `main`)

---

## Background the implementer needs

### Locked semantics

- `SyncError(transient=True)` default keeps every existing construction/branch green; only the client's 4xx fail-fast raise passes `transient=False` (a request-level rejection will never heal by waiting — retrying it 5m/15m/1h is pointless burn against a WAF-ban-prone endpoint).
- `ConfigError` behavior unchanged (never retries, surfaces as 503) — the issue's "config errors surface immediately" is already true at the repo boundary; this slice extends the same treatment to unrecoverable sync rejections and to the tick/preview render paths.
- `error` tick stage: `TickEventDTO.stage` is a plain `str`, so no DTO shape change — only the documented value set grows (`clock | countdown:* | playlist:* | error`). The display client already reloads on any stage change (`app.js` tick handler), landing on the route's 503/404 slate. No template or JS change needed — verify by reading `app.js:123-134` before assuming otherwise.
- `_tomorrow` invariant KEPT: `engine.py:210-217` deliberately never sources tomorrow from last-known (a past template must not seed tomorrow's Fajr — wrong data presented as correct). The issue lists it as context, not as a defect. Document the rationale; do not "fix" it.
- Overnight scope guard: no new error types, no DTO field changes, no template changes except the preview error line if the admin template needs it (check `playlists.html` preview rendering first — if it already handles `null`, render the error string in the same slot).

## File Structure

- Modify: `src/muhideen/core/errors.py` — `transient` flag on `SyncError`
- Modify: `src/muhideen/adapters/jakim_esolat.py` — 4xx raise passes `transient=False`
- Modify: `src/muhideen/adapters/scheduler.py` — non-transient no-retry + 6h long-pole chain + log wording
- Modify: `src/muhideen/api/app.py` — tick `error` stage + preview `error` shape
- Modify: `src/muhideen/views/templates/admin/playlists.html` — preview error line (only if null-slot exists; else reuse it)
- Test: `tests/integration/test_jakim_esolat.py` — 4xx error carries `transient=False`, still no in-client retry
- Test: `tests/integration/test_scheduler_sync.py` — update `test_gives_up_after_three_retries` (chain continues long-pole) + new retry-matrix tests
- Test: `tests/unit/test_errors.py` or nearest (grep `SyncError(` in `tests/unit`) — default-transient pin
- Test: tick/preview e2e (grep `tick` in `tests/e2e`, `preview` in tests) — corrupt config tick stage `error`, preview error shape, never `clock`
- Test: offline-first-no-coordinates e2e — fresh DB + no coords + unreachable JAKIM → `/display` slate + `/api/next-event` 503 (find the no-coords fixture pattern first)
- Docs: `docs/api-contract.md` (tick `error` value + preview error shape), `docs/deployment.md` (offline-first requirements: first boot needs connectivity or coordinates; `_tomorrow` rationale pointer), `CHANGELOG.md` Added entry

No migration, no locale change, no DTO shape change, no client JS change (verify, don't assume).

---

### Task 1: SyncError.transient + client 4xx marking

**Files:** `src/muhideen/core/errors.py`, `src/muhideen/adapters/jakim_esolat.py`, tests touching `SyncError(` construction (grep first)

- [ ] Failing tests: `SyncError("x", zone="z").transient is True` default pin (put in the unit file that already constructs `SyncError` — find via `grep -rln "SyncError(" tests/unit`); client test `test_4xx_marks_unrecoverable` next to `test_4xx_fails_fast_without_in_client_retry` (`tests/integration/test_jakim_esolat.py:281`): 4xx raises `SyncError` with `transient is False` and still performs no in-client retry. Run: `uv run pytest tests/integration/test_jakim_esolat.py -q -k "4xx or transient"` → Expected: FAIL (`AttributeError: transient`).
- [ ] Implement: `SyncError.__init__(self, message, zone="", date="", *, transient=True)` storing the flag (keep positional compatibility — existing `SyncError("...", zone=..., date=...)` calls must not churn; verify with `grep -rn "SyncError(" src/ | head -30`); client 4xx raise gains `transient=False` (only that raise — parse/network raises stay default). Verify: same command → PASS; `uv run pytest tests/integration/test_jakim_esolat.py tests/unit -q` → PASS.
- [ ] Lint touched py files (`ruff check` + `format --check`).

### Task 2: Scheduler policy — no-retry unrecoverable + 6h long-pole

**Files:** `src/muhideen/adapters/scheduler.py`, `tests/integration/test_scheduler_sync.py`

- [ ] Failing tests: `test_non_transient_sync_error_skips_retry` (client error `SyncError(transient=False)` → `sync_job` returns None, no `jakim-sync-retry-1` job, error-level log with zone + guidance words `check the zone`); `test_transient_failure_continues_long_pole_after_three_retries` (drive `retry-1..3` funcs as in `test_chained_failures_schedule_15m_then_1h`, then `retry-3.func()` schedules `jakim-sync-retry-long` at `now + 6h` with same misfire/coalesce flags, and a further long func re-schedules itself); update `test_gives_up_after_three_retries` (it pins silence — rewrite to the long-pole expectation, keep the attempt-count log assertion). Run: `uv run pytest tests/integration/test_scheduler_sync.py -q` → Expected: FAIL (no long job; gave-up log).
- [ ] Implement: in `sync_job`'s `except SyncError`, branch `if not exc.transient:` → `logger.error("jakim sync rejected (zone=%s): %s — check the zone code and JAKIM availability; no retry scheduled", ...)` + return None. Else existing chain, except the `attempt >= MAX_RETRIES` arm now schedules `jakim-sync-retry-long` at `clock.now() + timedelta(hours=6)` via `_add_job` (stable id so `replace_existing` collapses overlaps with the next 02:00 cron) and logs `… will retry in 6h`. Long job func is `sync_job(..., attempt=MAX_RETRIES)` so further failures re-arm the same 6h id. Module docstring: update the give-up sentence to the long-pole rule. Verify: same file → PASS.
- [ ] Lint touched files.

### Task 3: Tick + preview surface errors, never Clock

**Files:** `src/muhideen/api/app.py`, `src/muhideen/views/templates/admin/playlists.html` (if needed), tick/preview tests, `docs/api-contract.md` (tick stage values + preview error shape)

- [ ] Failing tests: e2e tick with corrupt settings (find the tick e2e via `grep -rln "tick" tests/e2e`; corrupt-settings helper pattern via `grep -rln "corrupt" tests/e2e` — reuse both): `/api/events` tick frame carries `"stage": "error"` (not `"clock"`); preview with corrupt settings returns an `error` reason instead of silent null (find preview tests via `grep -rln "preview" tests/` — assert the reason string mentions the config problem). Run: selected tests → Expected: FAIL (`"clock"` / `None`).
- [ ] Implement: `_event_stream` tick branch: `except ConfigError → stage = "error"`; keep other `MuhideenError` (ScheduleError — no schedule at all is equally unrenderable) → `"error"` as well, but log at distinct levels (ConfigError error-level with guidance, ScheduleError warning). Simplest faithful shape: `except MuhideenError as exc: stage = "error"` + `logger.error` mentioning fallback-to-slate (check current warning text at `app.py:421` and upgrade it). `_occupancy_preview`: replace `except (ConfigError, ScheduleError): preview = None` with an `{"error": str(exc)}` payload; template renders the string in the existing null slot (read `playlists.html` preview block first); `/api/playlists/preview` contract notes the additive `error` key. Contract docs: tick `stage` value list gains `error` (config/schedule failure — client reloads into the route slate). Verify: selected tests → PASS; `uv run pytest tests/e2e tests/contract -q` → PASS.
- [ ] Lint touched files (`node --check` if any JS touched — expected none).

### Task 4: Offline-first-no-coordinates pin + docs

**Files:** e2e test (same file as Task 3 tick tests if fixtures shared), `docs/deployment.md`, (read-only: `engine.py:210-217`)

- [ ] Failing test: fresh DB + settings without coords + JAKIM transport failing → `GET /display` renders a slate (503 or 404, assert slate marker not empty 200) and `GET /api/next-event?now=…` → 503. Find the e2e app-fixture pattern that builds a fresh DB with custom settings (grep `SqliteSettingsRepo` in `tests/e2e` or the `surface` fixture). Run → Expected: FAIL only if current behavior differs (if it already slates, the test pins the outcome — note that in the commit message like prior slices).
- [ ] Docs: `docs/deployment.md` offline-first section gains: first boot requires JAKIM reachability OR coordinates (else the documented slate outcome), plus the `_tomorrow`-never-last-known rationale pointer (wrong-data-as-correct). No code change in engine. Verify: full `uv run pytest -q` → PASS.
- [ ] Lint any touched test files.

### Task 5: Changelog + full gate

- [ ] CHANGELOG `[Unreleased] Added`: failure-visibility slice (tick `error` stage, preview error reason, sync `transient` split with 4xx no-retry + 6h long-pole, offline-first slate documented; `_tomorrow` invariant kept deliberately). No dev-plan filename reference.
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #44 → Tasks 1–2 (retry by error class incl. 4xx + long-pole), Task 3 (tick + preview surface, never Clock), Task 4 (no-coords outcome pinned + documented; `_tomorrow` kept with rationale). Live clock rebuild, per-display errors, new banners — excluded (route slates already exist).
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task; grep-first steps name fallbacks.
3. Type consistency: `transient: bool` keyword-only with default; stage stays `str`; preview gains optional `error: str` key (additive).
4. Momus dry gate: `errors.py:30-37` / `jakim_esolat.py:241-287` / `scheduler.py:40-43,129-183` / `app.py:327-336,409-427,989-1033` / `engine.py:210-217` / `dto.py:233-243` / `test_scheduler_sync.py:227-279` / `test_jakim_esolat.py:281-299` verified on disk this session; order fixed (error type → policy → render → docs); each task red→green with exact commands. `setup.html`/`admin.js`/`locales` untouched.
