# Manual schedule entry + year-boundary bridging — Implementation Plan (issue #43)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** An admin can pin a manual day schedule that outranks automatic sources, JAKIM sync never overwrites manual pins, and the December/January year-boundary gap is closed by manual bridging plus documented procedure.

**Architecture:** Manual days live in the existing `prayer_times` table as rows with `source='manual'` (column + `ScheduleSource.MANUAL` + display banner already exist — nothing ever writes them today); new admin-only `PUT /api/manual-day` (upsert, `HH:MM` + ordering validation via `domain.ordering.ensure_ordered`) + `DELETE /api/manual-day?date=` (release the pin); `run_sync` skips overwriting manual rows so the documented precedence (manual > JAKIM > calc) holds across syncs; the engine is untouched (cached-manual already wins the chain); admin settings UI gains a compact Manual Schedule section; deployment docs record the year-boundary procedure.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite, Jinja + vanilla JS, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #43; `src/muhideen/core/values.py:78-84` (`ScheduleSource` incl. `MANUAL`); `src/muhideen/domain/fallback.py:29-52` (chain: cached → calc → last-known); `src/muhideen/domain/ordering.py` (`ensure_ordered` — verify name/shape by reading first); `src/muhideen/adapters/scheduler.py:93-126` (`run_sync` save loop); `src/muhideen/adapters/sqlite_repo.py:108-146` (`get_day`/`save_day` upsert); `src/muhideen/migrations/0001_initial.sql:22` (source column); `src/muhideen/views/display.py:156-157` (MANUAL banner, already rendered); `src/muhideen/api/app.py:822-831` (PUT settings pattern), `app.py:552-561` (prayer-day route); `src/muhideen/api/dto.py` (`TimeHHMM`, `PrayerDayDTO`); `src/muhideen/views/templates/admin/settings.html` + `src/muhideen/static/admin.js` (settings sections + save bodies); `api/fixtures/prayer-day.json` (day shape); `docs/api-contract.md`, `docs/deployment.md`.

**Branch:** `feature/manual-schedule` (cut when approved; base on `main`)

---

## Background the implementer needs

### Locked semantics

- One zone per installation: manual days are stamped with `settings.zone` server-side (the request carries no zone — mirrors `seed.py` one-zone rule). Dates are plain `YYYY-MM-DD`; times are day-local `HH:MM` like JAKIM rows.
- Precedence is structural, not a flag: a manual row in `prayer_times` is returned by `get_day` and wins the existing cached-first chain with `stale=True` (non-JAKIM) plus the MANUAL banner. No engine change.
- Sync protection: `run_sync` checks `prayer_repo.get_day(day.date, zone)` per fetched day and skips saving when the stored row's `source is MANUAL` (365 indexed selects per yearly sync — negligible on local SQLite; do NOT add a batch query — keep the port surface unchanged). Deleting the pin re-exposes the date to the next sync (documented).
- Validation reuses `domain.ordering.ensure_ordered` (read its signature first — it guards JAKIM rows: `Imsak < Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha`); `ValueError` → 422. `fetched_at` = handler `now` (clock), so staleness ages honestly from pin time.
- Year-boundary: the JAKIM `period=year` endpoint cannot serve next-year rows in December (verified limitation, PRD §6.1), so no fetch strategy can close it — the fix is (1) manual bridging (the feature itself), (2) no behavior change to the graceful calc/last-known degradation, (3) a tested + documented procedure. Do NOT attempt endpoint parameter probing or fabricated rows.
- Non-goals: recurring/manual ranges (one day per call), manual weeks import, calc parameter editing, touching `_tomorrow`/last-known rules, locales changes.

## File Structure

- Modify: `src/muhideen/adapters/scheduler.py` — skip-manual-rows in `run_sync` (+ docstring)
- Modify: `src/muhideen/api/app.py` — `PUT /api/manual-day` + `DELETE /api/manual-day` (admin)
- Modify: `src/muhideen/api/dto.py` — `ManualDayDTO` (date + 8× `TimeHHMM`) with `to_prayer_day(zone, now)` mapper (ordering-validated)
- Modify: `src/muhideen/adapters/sqlite_repo.py` — only if a delete-pin helper is needed (check for existing delete first; `save_day` upsert already covers writes)
- Modify: `src/muhideen/views/templates/admin/settings.html` — Manual Schedule section (date + 8 time inputs + Save/Clear + status)
- Modify: `src/muhideen/static/admin.js` — section wiring + DEFAULTS untouched (no DTO settings change — guard tests must stay green)
- Fixtures: `api/fixtures/manual-day.json` (request + response samples mirroring `prayer-day.json` field style)
- Test: scheduler sync-skip test, API round-trip + 422 + precedence tests, admin save wiring test, December-bridge e2e (pinned FakeClock Dec date + manual Jan row served as manual)
- Docs: `docs/api-contract.md` (new routes section), `docs/deployment.md` (year-boundary procedure), `CHANGELOG.md` Added entry

No migration (rows + column exist), no engine change, no DTO settings change, no `setup.html` change.

---

### Task 1: Sync protection for manual rows

**Files:** `src/muhideen/adapters/scheduler.py`, `tests/integration/test_scheduler_sync.py` (same helpers `_defaults/_run_sync`, `FakePrayerRepo`)

- [ ] Failing test: `test_run_sync_skips_manually_pinned_days` — repo pre-seeded with a manual `PrayerDay` for one date (construct via `PrayerDay(..., source=ScheduleSource.MANUAL, fetched_at=...)` — read `FakePrayerRepo`/`_day` helpers first); `run_sync` with a client returning that date among others → manual row byte-identical afterwards (all 8 markers + source), other dates saved. Run: `uv run pytest tests/integration/test_scheduler_sync.py -q -k manual` → Expected: FAIL (row overwritten with JAKIM times).
- [ ] Implement: in the `run_sync` save loop, `existing = prayer_repo.get_day(day.date, settings.zone)`; `if existing is not None and existing.source is ScheduleSource.MANUAL: continue`. Docstring notes the precedence. Verify: same file → PASS.
- [ ] Lint touched files.

### Task 2: ManualDayDTO + routes + contract/fixtures

**Files:** `src/muhideen/api/dto.py`, `src/muhideen/api/app.py`, `api/fixtures/manual-day.json`, `docs/api-contract.md`, route tests (find playlist/manual-adjacent API test file via `grep -rln "PUT.*settings\|/api/settings" tests/e2e`)

- [ ] Failing tests: `PUT /api/manual-day` full 8-marker body → 200 echoing the day with `"source": "manual"`; second PUT same date replaces; `DELETE /api/manual-day?date=` → `{ok: true}` and the date falls back to auto (GET prayer-day no longer manual); invalid ordering (swap asr/maghrib) → 422; anonymous → 401. Contract parity file for the new fixture (mirror the prayer-day fixture test). Run: selected → Expected: FAIL (404 no route).
- [ ] Implement: `ManualDayDTO(ContractDTO)` with `date: date` + 8× `TimeHHMM`; `to_prayer_day(*, zone, now)` building `PrayerDay(source=MANUAL, fetched_at=now)` after `ensure_ordered` (import from `domain.ordering` — views/api may import domain? Layer rule is `views` never `domain`; api→domain is the established direction — verify no lint-imports violation by running it after). Routes admin-gated; PUT upserts via `prayer_repo.save_day`, DELETE via repo delete (add `delete_day(date, zone)` to the port + SQLite impl only if missing — grep first; keep it minimal). Response: `PrayerDayDTO.from_domain(day, stale=True, hijri_date=resolve_hijri(...))` mirroring the prayer-day route. Fixture + contract section (precedence + pin-release semantics). Verify: route tests + `tests/contract -q` → PASS.
- [ ] Lint touched files.

### Task 3: Precedence + December-bridge e2e

**Files:** engine/e2e tests (reuse Task 2 fixtures + `surface` pattern from `test_display.py`)

- [ ] Failing tests: `test_manual_pin_outranks_jakim_and_calc` — seed JAKIM row + coords + manual PUT for same date → `GET /api/prayer-day` returns manual times with `source == "manual"` and display shows MANUAL banner; `test_december_gap_bridged_by_manual_pin` — pinned clock in December, cache empty past 31-Dec, manual PUT for a January date → January resolves as manual (not 404, not last-known template). Run → Expected: FAIL (no manual route / JAKIM wins).
- [ ] Implement: no production code expected (Tasks 1–2 carry it) — if red persists, fix forward minimally and note it. Verify: selected → PASS; full `uv run pytest -q` → PASS.
- [ ] Lint touched test files.

### Task 4: Admin UI section + wiring

**Files:** `src/muhideen/views/templates/admin/settings.html`, `src/muhideen/static/admin.js`, `tests/e2e/test_admin_settings.py`, `tests/unit/test_admin_static.py` (guard must stay green)

- [ ] Failing test: settings-save e2e asserting the manual section posts `PUT /api/manual-day` correctly — simplest pin: extend the admin settings test with a manual PUT round-trip through the existing authed client (API covered in Task 2; here pin that the page renders the section: `id="manual-date"` + 8 inputs present in `/admin/settings` HTML). Run → Expected: FAIL (no section).
- [ ] Implement: Manual Schedule section (date input default today + 8 `HH:MM` time inputs + Save/Clear + status line, prefilled from a `GET /api/prayer-day?date=` fetch on date change — read existing admin.js fetch patterns first); Save → PUT, Clear → DELETE; **no DEFAULTS change** (no settings-DTO change — guard stays green by construction). Verify: targeted → PASS (`node --check admin.js` if node exists).
- [ ] Lint (`ruff` test file; visual/JS check).

### Task 5: Docs + changelog + full gate

- [ ] `docs/deployment.md`: year-boundary procedure (December gap cause → manual bridging steps → January auto-recovery via daily sync; release-pin deletion semantics). `docs/api-contract.md` already has routes (Task 2) — verify examples match fixtures. CHANGELOG `[Unreleased] Added`: manual entry + sync protection + December bridging (no dev-plan filename ref).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #43 → Tasks 1–2 (pin + protect + API), Task 3 (precedence + December bridge e2e), Task 4 (admin entry), Task 5 (procedure docs). Endpoint probing, ranges/import, calc editing, `_tomorrow` changes excluded with reasons.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task; grep-first steps name fallbacks.
3. Type consistency: `TimeHHMM` strings in → `time` objects in `PrayerDay` (mirror `IqamahRuleDTO.to_domain` `time.fromisoformat` pattern); `source` literal `"manual"` only constructed in one mapper.
4. Momus dry gate: `values.py:78-84` / `fallback.py:29-52` / `ordering.py` (read-first) / `scheduler.py:93-126` / `sqlite_repo.py:108-146` / `0001_initial.sql:22` / `display.py:156-157` / `app.py:822-831,552-561` verified on disk; order fixed (protection → API → e2e → UI → docs); each task red→green. `setup.html`/`locales`/engine untouched.
