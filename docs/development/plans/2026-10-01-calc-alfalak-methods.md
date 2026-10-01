# al-falak swap + real FR-1.3 method params — Implementation Plan (issues #35, #36)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: plan self-review below, `oracle` on the branch diff before PR handoff.

**Goal:** Replace `adhanpy==1.0.5` with `al-falak==1.0.0` with zero behavior change on the MABIMS path, then give each contract method (`MABIMS`/`MWL`/`ISNA`/`Egyptian`) its real parameters plus an end-to-end Asr juristic setting and wizard method parity, in one PR closing #35 and the method/Asr/wizard parts of #36.

**Architecture:** The `CalcEngine` port does not change; only `adapters/calc_mabims.py` is re-wired. MABIMS keeps the fitted custom angles (17.75/18.25, dhuhr +2 via `adjustments`, Shafi default) with `method=None`; the other three use al-falak built-in methods with their own `method_adjustments` and no extra tune. `AlFalakError` subclasses are translated to the existing `ValueError` cache-miss contract at the adapter boundary, so `engine.py` is untouched. New `Settings.asr_juristic` (`shafi`/`hanafi`, default `shafi`) flows through the same settings API the wizard uses; the wizard gains a method select (PRD FR-6.2 parity), Asr stays settings-only (advanced tuning, documented).

**Tech Stack:** Python 3.11+, uv, `al-falak==1.0.0` (MIT, zero runtime deps, PEP 561), ruff (88 cols), strict pyright on `src/`, import-linter layers `api → engine → adapters → domain → core`, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #35, issue #36; `src/muhideen/adapters/calc_mabims.py:1-129`; `src/muhideen/engine/engine.py:188-207` (`ValueError` → cache miss, untouched); `src/muhideen/core/values.py:210-307` (`Settings`); `src/muhideen/api/dto.py:378` (`method: MethodLiteral`); `src/muhideen/adapters/sqlite_repo.py:252,281` (method kv); `src/muhideen/views/templates/admin/settings.html:36,82-86` (method select); `src/muhideen/views/templates/admin/setup.html` (no method select — the gap); `src/muhideen/static/admin.js:117-153` (wizard body), `:300-335` (save body); `tests/integration/test_calc_mabims.py` (golden vectors, `GOLDEN_TOLERANCE_MIN=5`); al-falak 1.0.0 PyPI + migration guide (`adhanpy` → `alfalak` root imports, error-hierarchy table); al-falak clone at `~/dev/nexusnv/al-falak` (`calculation/MethodsParameters.py`, `calculation/CalculationParameters.py:16-32,92-101`, `PrayerTimes.py:96-103,412-421`, `calculation/Madhab.py`, `exceptions.py`); `ARCHITECTURE.md:31` (only-MABIMS disclaimer to update); `CONTRIBUTING.md:38-51` (quality gate, contract changes ship fixtures + doc + changelog).

**Branch:** `feature/calc-alfalak`

---

## Background the implementer needs

### al-falak facts (verified against the clone + PyPI, 2026-10-01)

- Same `batoulapps/adhan` port lineage as adhanpy; public surface keeps class/method names. Swap = change install + import root (`from alfalak import PrayerTimes, CalculationParameters, Madhab, PrayerAdjustments`). Internal module paths also work; prefer root imports per the migration guide.
- `CalculationParameters(method=None, adjustments=..., fajr_angle=..., isha_angle=...)`: `method=None` → `NONE`, custom angles kept. Passing a method applies that method's template (`_set_parameters_using_method`); `adjustments` and `method_adjustments` are **summed** (`PrayerTimes.py:412-421`), so the dhuhr +2 tune must stay MABIMS-only or every method gets +3 on dhuhr.
- Built-in angles (`MethodsParameters.py`): `MUSLIM_WORLD_LEAGUE` 18.0/17.0, `EGYPTIAN` 19.5/17.5, `NORTH_AMERICA` 15.0/15.0 (this is ISNA; contract keeps the name `ISNA`), `SINGAPORE` 20.0/18.0. Each non-custom template carries its own `method_adjustments` (e.g. dhuhr=1) — do not add ours.
- Errors: `AlFalakError` base with `AstronomicalError` (polar day/night, undefined Asr), `ConfigurationError` (bad method/madhab/polar rule), `ValidationError` (coordinates within ±90/±180, angles within 0–90, non-negative isha interval). Internal isha-interval `ValueError` untouched. PrayerTimes returns UTC datetimes; `time_zone` param name unchanged.
- Rounding fix in v1.0.0 (hour-rollover, half-up at 30s, zero microseconds): golden compares minute precision, expect green; any shift gets recorded, tolerance stays 5.

### Design decisions (locked)

1. Contract method names unchanged (`MABIMS`/`MWL`/`ISNA`/`Egyptian`); `ISNA` maps to `NORTH_AMERICA` with a comment. No new methods (no Umm-al-Qura etc.).
2. `asr_juristic: Literal["shafi","hanafi"] = "shafi"` on `Settings`; adapter maps to `Madhab.SHAFI/HANAFI`. Core stays free of the alfalak import (adapter owns mapping).
3. Per-install timezone is OUT of this PR (device-clock composition: `SystemClock`, scheduler cron, seed — separate slice). File a follow-up issue at PR time and note it in #36.
4. Qibla/SunnahTimes/polar rules/CLI: not wired. `cycle`/`image` caps untouched.

## File Structure

- Modify: `pyproject.toml` — remove `adhanpy==1.0.5`, add `al-falak==1.0.0`; regenerate `uv.lock`
- Modify: `src/muhideen/adapters/calc_mabims.py` — alfalak imports, `_params(method, madhab)` per-method mapping, `AlFalakError` → `ValueError`, docstring
- Modify: `src/muhideen/core/values.py` — `AsrJuristic` literal + `Settings.asr_juristic`
- Modify: `src/muhideen/api/dto.py` — `SettingsDTO.asr_juristic`
- Modify: `src/muhideen/adapters/sqlite_repo.py` — `asr_juristic` kv load/save (default `shafi`)
- Modify: `src/muhideen/views/templates/admin/settings.html` — Asr select; `setup.html` — method select
- Modify: `src/muhideen/static/admin.js` — wizard body includes method; save body includes asr_juristic
- Modify: `api/fixtures/settings.json`, `docs/api-contract.md`, `CHANGELOG.md` — additive `asr_juristic`
- Test: `tests/integration/test_calc_mabims.py` — replace fallback test with distinctness tests, add Asr test
- Test: `tests/integration/test_sqlite_settings_repo.py`, `tests/unit/test_core_values.py`, `tests/unit/test_admin_static.py`, `tests/e2e/test_admin_settings.py`, `tests/e2e/test_admin_pages.py` (wizard)

No migration file (additive kv key falls back to default, same precedent as `adhan_duration_s`). No engine/domain change.

---

### Task 1: Dependency swap with identical MABIMS behavior

**Files:** `pyproject.toml`, `uv.lock`, `src/muhideen/adapters/calc_mabims.py:25-53,101-106`

**Goal:** al-falak installed, MABIMS output bit-identical, library errors mapped to the port contract.

- [ ] Failing test first: run `uv run pytest tests/integration/test_calc_mabims.py -q` after swapping the import lines to `alfalak` (package not yet installed) → Expected: FAIL (`ModuleNotFoundError`). Then `uv add al-falak==1.0.0` + remove adhanpy + `uv lock`.
- [ ] Implement: root imports (`from alfalak import CalculationParameters, Madhab, PrayerAdjustments, PrayerTimes` + `from alfalak import AlFalakError` for mapping); `_params()` unchanged values; wrap the `PrayerTimes(...)` call so `AlFalakError` becomes `ValueError(str)` (keep message); keep explicit `ValueError`s (unknown method, offset ranges); update module docstring (library name/version/lineage, 6-marker note preserved). Verify: `uv run pytest tests/integration/test_calc_mabims.py -q` → PASS with zero golden changes; `rg -n "adhanpy" src/ tests/ pyproject.toml` → 0 hits (plans/research docs excluded — ephemeral per `docs/development/AGENTS.md`).

### Task 2: Real per-method parameters

**Files:** `src/muhideen/adapters/calc_mabims.py:40-53`, `tests/integration/test_calc_mabims.py:124-130`

**Goal:** Each contract method computes its own reference parameters; MABIMS untouched.

- [ ] Failing test: replace `test_contract_methods_fall_back_to_mabims_parameters` with `test_contract_methods_use_own_parameters` asserting each of MWL/ISNA/Egyptian differs from MABIMS on ≥1 of the 8 markers for the 2026-09-23 SGR01 vector, plus `test_isna_maps_to_north_america` (ISNA fajr later than MWL fajr: 15° vs 18°) and `test_dhuhr_tune_applies_to_mabims_only` (MABIMS dhuhr == pure-transit +2; MWL dhuhr == transit +1 from its own method_adjustments). Run → Expected: FAIL (all equal today).
- [ ] Implement: `_params(method: str)` returning `CalculationParameters(fajr_angle=17.75, isha_angle=18.25, adjustments=PrayerAdjustments(dhuhr=2))` for MABIMS vs `CalculationParameters(method=<BUILTIN>)` for the other three (`MUSLIM_WORLD_LEAGUE`, `NORTH_AMERICA` for ISNA with comment, `EGYPTIAN`); madhab applied after construction from a parameter (Task 3 wires settings; default Shafi now). Keep `_SUPPORTED_METHODS` + unknown-method `ValueError`. Verify: new tests PASS; all 6 golden MABIMS vectors still within tolerance 5 with no test edits besides the replaced test; paste per-marker max deviation per method in the PR description.

### Task 3: Asr juristic end-to-end

**Files:** `src/muhideen/core/values.py`, `src/muhideen/api/dto.py`, `src/muhideen/adapters/sqlite_repo.py`, `settings.html`, `admin.js` save body

**Goal:** Admin-selectable Shafi/Hanafi that visibly moves Asr and nothing else.

- [ ] Failing test: add `test_asr_juristic_hanafi_delays_asr_only` to `tests/integration/test_calc_mabims.py` (same day/coords/method, hanafi Asr strictly later than shafi Asr, all other 7 markers equal) + `test_asr_juristic_round_trip` to `tests/integration/test_sqlite_settings_repo.py` + DTO round-trip to `tests/e2e/test_admin_settings.py`. Run → Expected: FAIL (`TypeError: unexpected keyword 'asr_juristic'` / `ImportError`).
- [ ] Implement: `AsrJuristic = Literal["shafi","hanafi"]`, `Settings.asr_juristic = "shafi"` (no range guard needed — Literal + DTO enum enforce); `SettingsDTO.asr_juristic`; sqlite kv `asr_juristic` load default `shafi` + save; `_params` sets `params.madhab` from the passed juristic; settings.html Asr select (Shafi standard/Hanafi) + save body field; fixtures + contract examples + CHANGELOG Added entry. Verify: new tests PASS; `uv run pytest tests/contract tests/e2e/test_admin_settings.py -q` → PASS.

### Task 4: Wizard method parity

**Files:** `src/muhideen/views/templates/admin/setup.html`, `src/muhideen/static/admin.js`, `tests/unit/test_admin_static.py`, `tests/e2e/test_admin_pages.py`

**Goal:** First-boot wizard can set the calculation method (PRD FR-6.2).

- [ ] Failing test: extend `tests/unit/test_admin_static.py` (wizard body references the method field id) + e2e wizard test posting a wizard-style body with `"method": "MWL"` asserting stored settings round-trip. Run → Expected: FAIL (no `w-method`/`s-method` equivalent in wizard).
- [ ] Implement: `setup.html` method `<select>` (same 4 options/labels as settings), wizard body includes `method` via `settingsBodyFromDefaults` overrides (DEFAULTS already carries `"method": "MABIMS"` so omission still defaults). Verify: `uv run pytest tests/unit/test_admin_static.py tests/e2e/test_admin_pages.py tests/e2e/test_admin_playlists.py -q` → PASS (playlist tests pin wizard DEFAULTS reuse).

### Task 5: Docs + full gate + follow-ups

**Files:** `ARCHITECTURE.md:31`, `CHANGELOG.md`, issues

- [ ] Update the only-MABIMS disclaimer to describe the new per-method behavior + ISNA→North-America mapping note. CHANGELOG `[Unreleased] Added`: al-falak swap + per-method params + asr_juristic + wizard method.
- [ ] File the per-install-timezone follow-up issue (device-clock composition slice) and note it on #36; update #36 body scope if needed. Verify `rg -n "docs/development" src/ docs/api-contract.md PRD.md ARCHITECTURE.md CHANGELOG.md` → 0 hits.
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; purity scans → 0 hits; `uv run pytest -q` → green, coverage ≥95; `uv run tools/lint_theme.py --theme classic-green` → ok; `uv sync --locked --all-extras` resolves.

## Self-review

1. Spec coverage: #35 (swap + mapping + golden) → Tasks 1–2,5. #36 methods + Asr + wizard-method → Tasks 2–4. #36 timezone → explicitly deferred with follow-up issue (device-clock slice, not silently dropped). Qibla/polar/CLI excluded by name.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task — every step names files, tests, commands.
3. Type consistency: `method: str` in adapter (contract strings), `AsrJuristic` literal across values/DTO/repo, `Madhab` mapping in adapter only, `PrayerDay` shapes unchanged.
4. Momus dry gate: all file:line references verified on disk this session except `setup.html` method select and `settings.html` Asr select (they are the work, tests pin them); each task starts with a failing test + exact `uv run` command + expected output; QA per task names tool, steps, expected result.
