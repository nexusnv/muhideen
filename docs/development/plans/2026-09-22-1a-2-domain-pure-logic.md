# Slice 1A-2 Domain Pure Logic Implementation Plan

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Land `src/muhideen/domain/` as pure functions over `(now, schedule, settings)` — iqamah resolution, fallback chain with freshness flags, Hijri offset, and the PRD §8 state machine including Syuruq/Jumuah/midnight edges — proven by pinned-time unit tests.

**Architecture:** `domain/` layer only. Imports `muhideen.core` values/ports/errors plus stdlib `datetime`/`timedelta`/`date`. No `engine/`, `adapters/`, `api/`, `views/` changes. No wall-clock reads, no framework, no SQLite, no HTTP. `Clock` port is not read here; callers pass `now: datetime` explicitly so every test pins time exactly.

**Tech Stack:** Python 3.11+, uv, FastAPI-sync + Uvicorn 1 worker (untouched), ruff, strict pyright, import-linter, pytest (unit/contract/integration/property/e2e).

**References:** `docs/development/PHASES_AND_SLICES.md:37` slice 1A-2 row, `src/muhideen/core/values.py:18-86` enums + frozen dataclasses, `src/muhideen/core/ports.py:15-55` port surfaces (`Clock.now/monotonic`), `src/muhideen/core/errors.py:16-31` `ScheduleError`/`SyncError` context fields, `src/muhideen/core/__init__.py:1-49` public re-exports, `src/muhideen/domain/__init__.py` empty placeholder, `ARCHITECTURE.md:33-38` Core/Domain ownership + purity rule, `ARCHITECTURE.md:57-62` strict pyright + import-linter + purity scans, `PRD.md:73-82` FR-1.1–FR-1.6 (stale 48h, fallback order, iqamah defaults, Hijri offset -2..2), `PRD.md:335-345` §8 state machine + Syuruq/Jumuah/midnight rules, `TESTING_STRATEGY.md:7-13` test layers, `TESTING_STRATEGY.md:17-20` pinned `FakeClock` + file-local fakes, `docs/api-contract.md:21-37` `next-event` shape domain output must stay compatible with, `api/fixtures/next-event.json:1` IQAMAH_COUNTDOWN vector, `pyproject.toml:33-46` pytest markers + coverage `fail_under=95`, `pyproject.toml:58-87` ruff/pyright/import-linter config, `docs/adr/0003-sqlite-behind-ports.md:9-11` ports decision, `CONTEXT.md:24-55` Adhan/Iqamah/Syuruq/Jumuah/Zone/Stale terms.

**Branch:** `feature/1a-2-domain-pure-logic` (from `main`)

**Slice:** 1A-2 Domain pure logic. Entry: 1A-1 landed (`96fc2f7`). Exit: `unit` green including pinned-time determinism test; purity scan `rg` over `domain/` returns 0 hits.

---

## File Structure

- Create: `src/muhideen/domain/iqamah.py` — `resolve_iqamah(prayer, adhan_at, rules)` pure target computation
- Create: `src/muhideen/domain/fallback.py` — `FallbackResult` frozen dataclass, `STALE_AFTER`, `is_stale(day, now)`, `resolve_day(requested, zone, now, cached, calculated, last_known)`
- Create: `src/muhideen/domain/hijri.py` — `apply_hijri_offset(base_hijri, offset_days)` bounded shift
- Create: `src/muhideen/domain/prayer_state.py` — `PRE_ADHAN_WINDOW`, `resolve_next_event(now, today, tomorrow, rules, settings, stale)` full §8 machine
- Modify: `src/muhideen/domain/__init__.py` — re-export `resolve_iqamah`, `FallbackResult`, `is_stale`, `resolve_day`, `apply_hijri_offset`, `PRE_ADHAN_WINDOW`, `resolve_next_event`
- Test: `tests/unit/test_domain_iqamah.py` — delay/fixed/Syuruq/missing-rule vectors
- Test: `tests/unit/test_domain_fallback.py` — cached/calc/last-known/all-miss + 48h vectors
- Test: `tests/unit/test_domain_hijri.py` — offset -2..+2 and out-of-range rejection
- Test: `tests/unit/test_domain_prayer_state.py` — NORMAL/PRE_ADHAN/ADHAN/IQAMAH_COUNTDOWN/SALAH_DIM windows + Syuruq/Jumuah/midnight edges
- Test: `tests/unit/test_domain_determinism.py` — same now+snapshot+settings yields identical `NextEvent`

No `src/muhideen/engine/`, `adapters/`, `api/`, `views/` changes. No `docs/api-contract.md` change. No `api/fixtures/` changes (contract diff: `none`).

---

### Task 1: Iqamah resolution — failing guard then pure function

**Files:** `tests/unit/test_domain_iqamah.py` (create), `src/muhideen/domain/iqamah.py` (create)

- [ ] Add failing tests: `test_delay_adds_minutes`, `test_fixed_uses_clock_time`, `test_syuruq_returns_none`, `test_missing_rule_raises_config_error`, `test_fixed_without_time_raises_config_error`. Each builds tz-aware `adhan_at` (`Asia/Kuala_Lumpur`), explicit `dict[PrayerName, IqamahRule]` covering `fajr/dhuhr/asr/maghrib/isha/jumuah` delay values from PRD FR-1.4 (Subuh 15, Dhuhr 10, Asr 10, Maghrib 10, Isha 15). Run: `uv run pytest tests/unit/test_domain_iqamah.py -v` → Expected: FAIL (`ModuleNotFoundError`, no `iqamah` module).
- [ ] Implement `resolve_iqamah(prayer: PrayerName, adhan_at: datetime, rules: dict[PrayerName, IqamahRule]) -> datetime | None` in `src/muhideen/domain/iqamah.py`. Rules: `SYURUQ` returns `None` without rule lookup; `delay` returns `adhan_at + timedelta(minutes=rule.delay_minutes)`; `fixed` returns `rule.fixed_time` combined with `adhan_at.date()` under `adhan_at.tzinfo`, raising `ConfigError` when `fixed_time is None`; unknown prayer key raises `ConfigError`. Imports only `muhideen.core.errors.ConfigError`, `muhideen.core.values.IqamahRule/PrayerName`, stdlib `datetime/timedelta/time`. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.

**Depends on:** 1A-1 `IqamahRule` shape (`src/muhideen/core/values.py:67-72`).

### Task 2: Fallback chain + freshness flags

**Files:** `tests/unit/test_domain_fallback.py` (create), `src/muhideen/domain/fallback.py` (create)

- [ ] Add failing tests: `test_cached_fresh_hit_not_stale`, `test_cached_old_marks_stale`, `test_calc_fallback_marks_stale`, `test_last_known_marks_stale`, `test_all_miss_raises_schedule_error`, `test_zone_mismatch_ignored`, `test_is_stale_48h_boundary`. Pin `now` as explicit tz-aware `datetime` (file-local `FakeClock` helper returning fixed `now`/`monotonic`, defined inside this test file only). Cached/calculated fixtures use `PrayerDay` shape from `src/muhideen/core/values.py:42-53` with distinct `fetched_at` ages (1h vs 72h) and `ScheduleSource` values (`JAKIM` vs `CALC`). Run: `uv run pytest tests/unit/test_domain_fallback.py -v` → Expected: FAIL (no module).
- [ ] Implement `src/muhideen/domain/fallback.py`: frozen `FallbackResult(day: PrayerDay, stale: bool)` with `slots=True`; `STALE_AFTER = timedelta(hours=48)`; `is_stale(day, now)` returns `True` when `(now - day.fetched_at) > STALE_AFTER` or `day.source is not ScheduleSource.JAKIM`; `resolve_day(requested: date, zone: str, now: datetime, cached: PrayerDay | None, calculated: PrayerDay | None, last_known: PrayerDay | None) -> FallbackResult` with priority cached (requires `date == requested` and `zone` match) → calculated (same match) → last_known (requires `zone` match, any date) → raise `ScheduleError(message, zone, requested.isoformat())`. `stale` on the result comes from `is_stale(chosen, now)` except last_known path forces `True`. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.

**Depends on:** Task 1 file exists (shared `domain/` package import path only, no value flow).

### Task 3: Hijri offset application

**Files:** `tests/unit/test_domain_hijri.py` (create), `src/muhideen/domain/hijri.py` (create)

- [ ] Add failing tests: `test_offset_zero_identity`, `test_offset_plus_two`, `test_offset_minus_two`, `test_offset_out_of_range_raises`. Base Hijri date pinned as `date(1447, 3, 29)`-style fixed value; offsets `-2..+2` asserted by exact date arithmetic. Run: `uv run pytest tests/unit/test_domain_hijri.py -v` → Expected: FAIL (no module).
- [ ] Implement `apply_hijri_offset(base_hijri: date, offset_days: int) -> date` returning `base_hijri + timedelta(days=offset_days)`, raising `ConfigError` when `offset_days` outside `-2..2`. Full Umm-al-Qura/tabular calendar computation stays out of scope until slice 1A-6; this function shifts an already-computed Hijri date so the offset path is pinned now. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.

**Depends on:** Task 1 (package path only).

### Task 4: State machine core windows

**Files:** `tests/unit/test_domain_prayer_state.py` (create), `src/muhideen/domain/prayer_state.py` (create)

- [ ] Add failing tests for a fixed weekday (Wednesday) `PrayerDay` (SGR01, tz `Asia/Kuala_Lumpur`, fajr 05:45 syuruq 06:55 dhuhr 12:15 asr 15:30 maghrib 18:05 isha 19:25, source `JAKIM`): `test_normal_mid_morning`, `test_pre_adhan_five_minute_window`, `test_adhan_overlay_uses_settings_duration`, `test_iqamah_countdown_targets_rule`, `test_salah_dim_uses_default_minutes`. Rules dict uses delay mode (Dhuhr 10); `Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0, adhan_duration_s=180, dim_minutes_default=20, dim_minutes_jumuah=45)`. Each test passes explicit `now` (file-local `FakeClock.now()`), asserts `NextEvent` state + `next_prayer` + `adhan_at`/`iqamah_at`/`dim_until` + `stale` passthrough. Field shapes mirror `docs/api-contract.md:21-37` so 1A-3 DTOs map without renames. Run: `uv run pytest tests/unit/test_domain_prayer_state.py -v` → Expected: FAIL (no module).
- [ ] Implement `PRE_ADHAN_WINDOW = timedelta(minutes=5)` and `resolve_next_event(now: datetime, today: PrayerDay, tomorrow: PrayerDay | None, rules: dict[PrayerName, IqamahRule], settings: Settings, stale: bool) -> NextEvent`. Build ordered adhan datetimes from `today` times combined with `now.tzinfo`; resolve each `iqamah_at` via Task 1 function; `dim_until = iqamah_at + timedelta(minutes=settings.dim_minutes_default)`; window priority per adhan in chronological order: `[adhan, adhan+adhan_duration)` → `ADHAN`; `[adhan+duration, iqamah)` → `IQAMAH_COUNTDOWN`; `[iqamah, iqamah+dim)` → `SALAH_DIM`; `[adhan-5m, adhan)` → `PRE_ADHAN`; else → `NORMAL` toward next adhan. During `ADHAN`/`IQAMAH_COUNTDOWN`/`SALAH_DIM`, `next_prayer` names the prayer in progress with precomputed `adhan_at`/`iqamah_at`/`dim_until`; during `NORMAL`/`PRE_ADHAN`, `next_prayer` names the upcoming adhan with the same three targets precomputed. `now` echoes into `NextEvent.now`; caller-supplied `stale` echoes into `NextEvent.stale`. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.

**Depends on:** Task 1 (`resolve_iqamah`), Task 2 (`stale` flag meaning).

### Task 5: State machine edges — Syuruq, Jumuah, midnight crossover

**Files:** `tests/unit/test_domain_prayer_state.py` (extend), `src/muhideen/domain/prayer_state.py` (extend)

- [ ] Add failing tests: `test_syuruq_overlay_then_normal_no_dim`, `test_syuruq_never_produces_iqamah_targets`, `test_jumuah_replaces_dhuhr_friday`, `test_jumuah_uses_45m_dim`, `test_midnight_crossover_next_day_fajr`, `test_midnight_crossover_uses_tomorrow_schedule`. Syuruq vectors assert `iqamah_at is None`, `dim_until is None`, and state returns to `NORMAL` after `adhan_duration_s` with no `IQAMAH_COUNTDOWN`/`SALAH_DIM` window. Friday vector sets `today.date` to a Friday and asserts `next_prayer == JUMUAH` with Jumuah rule applied to the Dhuhr clock time. Midnight vectors set `now` past Isha dim and assert `next_prayer == FAJR` with `adhan_at.date() == today.date + 1 day`; when `tomorrow` is supplied its times are used, when `None` the Fajr clock time is carried from `today.fajr` plus one day. Run: `uv run pytest tests/unit/test_domain_prayer_state.py -v` → Expected: FAIL (edge branches missing).
- [ ] Extend `resolve_next_event`: Syuruq slot gets `ADHAN` overlay for `settings.adhan_duration_s` then falls through to `NORMAL` (skip countdown/dim branches); Friday detection via `today.date.weekday() == 4` relabels the Dhuhr slot to `JUMUAH` and selects `settings.dim_minutes_jumuah` for its dim length; post-Isha-dim `now` resolves to next-day Fajr per the synthesis rule above. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.

**Depends on:** Task 4 (core windows present).

### Task 6: Determinism proof, package exports, purity + full gate

**Files:** `tests/unit/test_domain_determinism.py` (create), `src/muhideen/domain/__init__.py` (modify)

- [ ] Add failing-then-passing determinism test `test_same_inputs_identical_output`: fixed `now`, fixed `PrayerDay` snapshot, fixed `Settings`, fixed rules dict → call `resolve_next_event` twice and assert full `NextEvent` equality (all seven fields); second case replays the `api/fixtures/next-event.json:1` vector (`2025-10-20T11:45:00+08:00` → `IQAMAH_COUNTDOWN` dhuhr) twice and asserts identical output. File-local `FakeClock` only. Run: `uv run pytest tests/unit/test_domain_determinism.py -v` → Expected: FAIL before `__init__` exports exist (import path), then PASS after.
- [ ] Export public names from `src/muhideen/domain/__init__.py`: `resolve_iqamah`, `FallbackResult`, `STALE_AFTER`, `is_stale`, `resolve_day`, `apply_hijri_offset`, `PRE_ADHAN_WINDOW`, `resolve_next_event`. Then run gates in order: `uv run pytest -m unit -q` → PASS; purity scan `rg -n "fastapi|sqlite3|httpx|datetime\.now|time\.time" src/muhideen/domain/` → 0 hits; `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → all green; `uv run pytest --cov=muhideen --cov-report=term-missing` → coverage gate `fail_under=95` holds with the combined core + domain suite.

**Depends on:** Tasks 1–5 (all domain modules present).

---

## Detailed-Plan Checklist

1. **PRD refs:** FR-1.1 stale 48h + FR-1.2 fallback order (`PRD.md:73-82`), FR-1.4 iqamah defaults, FR-1.5 Hijri offset range, FR-2.3/FR-2.4 countdown + dim durations, §8 machine (`PRD.md:335-345`).
2. **Contract diff:** `none`. No `docs/api-contract.md` edit, no `api/fixtures/` edit. Domain returns core `NextEvent`; 1A-3 maps it to DTOs. Fixtures reviewed for shape compatibility only.
3. **Changes with ownership:** backend-owned `domain/` only (4 new modules + `__init__` exports). No adapter/API/view changes. Frontend untouched.
4. **Tests by layer + new invariants:** `unit` only (5 files, ~20 tests). New invariants: delay/fixed iqamah math; 48h stale boundary; offset bounds; 5-minute PRE_ADHAN; adhan-duration overlay; Syuruq never dims; Jumuah replaces Dhuhr Friday with 45m dim; midnight resolves next-day Fajr; determinism replay.
5. **Purity/import-linter impact:** `domain/` imports `muhideen.core` + stdlib `datetime` only. Purity scan must show 0 hits. Layer contract `adapters → domain → core` unchanged; `lint-imports` stays green.
6. **Docs touched:** none beyond this plan. `CONTEXT.md` glossary unchanged (no new terms); no ADR (no hard-to-reverse choice; Hijri calendar library choice deferred to 1A-6).
7. **Rollback:** delete the 4 created `domain/*.py` files plus 5 test files and revert `domain/__init__.py` to empty. No migration, no contract bump, no fixture revert needed.
8. **Exit gate command output:** paste `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` result plus the `rg` purity output and the coverage line in the PR description.

## Out of Scope (later slices)

- Pydantic DTOs + fixture parity (1A-3), engine orchestration + `Clock` wiring (1A-4), SQLite/JAKIM/calc adapters + real Hijri tables (1A-5/1A-6), HTTP/SSE handlers (1A-7), service/installer (1A-8), any `views/` or theme work (1B).
