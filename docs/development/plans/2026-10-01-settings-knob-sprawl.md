# Collapse the Settings knob sprawl — Implementation Plan (issue #31)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `paxman-momus-review` (plan), `paxman-oracle-review` (after impl).

**Goal:** Make the Settings value module the single owner of theme-knob defaults and closed-enum rules so wire, stored-row, presentation, and script copies read from one seam and adding one knob touches one table plus its render.

**Architecture:** Extend `core/values.py` with `THEME_DEFAULTS`, `THEME_CHOICES`, and `THEME_KV_KEYS` tables plus `theme_default()`, `theme_choices()`, `theme_pairs()`, `theme_from_kv()` helpers; `api/dto.py` `ThemeDTO`, `adapters/sqlite_repo.py` `_theme_from_kv`/`_theme_pairs`/`DISPLAY_SETTINGS_ALLOWLIST`/`_check_theme_value`, and `views/display.py` class string all delegate to it; `static/admin.js` collapses its two hand-built bodies to clone `DEFAULTS` and a static test pins `DEFAULTS` values plus both body field sets against `SettingsDTO` so drift fails in CI. Scope is the seven theme knobs only; non-theme settings keys keep their current mapping (pattern proven, not re-cut).

**Tech Stack:** Python 3.11+, uv, ruff (88 cols), strict pyright on `src/`, import-linter layers `api → engine → adapters → domain → core` plus `views` presentation-only (DTOs + core, never domain), pytest markers `unit/contract/integration/e2e`.

**References:** Issue #31; `src/muhideen/core/values.py:152-208` (`ThemeSettings` defaults + `__post_init__`); `src/muhideen/core/values.py:210-232` (`Settings.theme`); `src/muhideen/api/dto.py:317-431` (`ThemeDTO`, `SettingsDTO`); `src/muhideen/adapters/sqlite_repo.py:177-227` (`DISPLAY_SETTINGS_ALLOWLIST`, `_theme_from_kv`, `_theme_pairs`); `src/muhideen/adapters/sqlite_repo.py:246-360` (`SqliteSettingsRepo`); `src/muhideen/adapters/sqlite_repo.py:408-431` (`_check_theme_value`); `src/muhideen/views/display.py:153-174` (knob reads); `src/muhideen/static/admin.js:12-45` (`DEFAULTS`); `src/muhideen/static/admin.js:117-153` (wizard body); `src/muhideen/static/admin.js:291-335` (save body); `ARCHITECTURE.md:63-77` (layers); `ARCHITECTURE.md:149-160` (new setting rule); `CONTEXT.md:81-82` (Theme); `CONTRIBUTING.md:38-51` (quality gate); `TESTING_STRATEGY.md`.

**Branch:** `feature/settings-knob-sprawl`

---

## File Structure

- Modify: `src/muhideen/core/values.py` — add `THEME_DEFAULTS`, `THEME_CHOICES`, `THEME_KV_KEYS`, `theme_default`, `theme_choices`, `theme_pairs`, `theme_from_kv`; `ThemeSettings.__post_init__` reads `THEME_CHOICES`
- Modify: `src/muhideen/api/dto.py` — `ThemeDTO.from_domain`/`to_domain` delegate to the seam (same wire shape)
- Modify: `src/muhideen/adapters/sqlite_repo.py` — `_theme_from_kv`, `_theme_pairs`, `DISPLAY_SETTINGS_ALLOWLIST`, `_check_theme_value` delegate to the seam
- Modify: `src/muhideen/views/display.py` — `body_class` built via `theme_css_class(theme)` helper from the seam (same string)
- Modify: `src/muhideen/static/admin.js` — add `settingsBodyFromDefaults()` cloning `DEFAULTS`; wizard and save bodies call it
- Test: `tests/unit/test_core_values.py` — seam tables pin defaults + choices
- Test: `tests/integration/test_sqlite_settings_repo.py` — round-trip + corrupt still `ConfigError`
- Test: `tests/unit/test_admin_static.py` — `DEFAULTS` values equal seam + both bodies cover `SettingsDTO` fields
- Test: `tests/unit/test_display_context.py` — `body_class` parity

No migration; no wire change; no `docs/api-contract.md` or `api/fixtures/` change (defaults unchanged).

---

### Task 1: Single owner tables in the value module

**Files:** `src/muhideen/core/values.py:152-208`, `tests/unit/test_core_values.py`

**Goal:** One table owns each knob's default and closed choices; `ThemeSettings` validates from it.

- [ ] Failing test: add `test_theme_seam_tables_are_single_source` (asserts `THEME_DEFAULTS == {"palette":"classic-green","font":"outfit","countdown_style":"boxes","clock_format":"24h-seconds","hijri_form":"long","boundary_strip":"show","density":"comfortable"}`, `THEME_CHOICES["palette"] == ("classic-green","midnight","sand")` plus the other six tuples, `theme_from_kv({}) == ThemeSettings()`, `theme_pairs(ThemeSettings())` has seven `theme.*` rows). Run: `uv run pytest tests/unit/test_core_values.py -k theme_seam -v` → Expected: FAIL (`ImportError: THEME_DEFAULTS`).
- [ ] Implement: add `THEME_DEFAULTS: dict[str,str]`, `THEME_CHOICES: dict[str,tuple[str,...]]`, `THEME_KV_KEYS: tuple[str,...]` (`theme.*` keys), `theme_default(knob)`, `theme_choices(knob)`, `theme_pairs(theme)`, `theme_from_kv(kv)` to `src/muhideen/core/values.py`; rewrite `ThemeSettings.__post_init__` to loop `THEME_CHOICES` (same messages). Keep field defaults identical so no fixture drift. Verify: `uv run pytest tests/unit/test_core_values.py -q` → PASS; `uv run pyright src/muhideen/core/values.py` → green.

### Task 2: Wire plus stored-row mapping read from the seam

**Files:** `src/muhideen/api/dto.py:317-351`, `src/muhideen/adapters/sqlite_repo.py:177-227`, `src/muhideen/adapters/sqlite_repo.py:408-431`

**Goal:** Deleting any one mapper no longer matters — all three read the same tables.

- [ ] Failing test: add `test_theme_mappers_share_seam_tables` to `tests/integration/test_sqlite_settings_repo.py` (asserts `_theme_pairs(ThemeSettings()) == theme_pairs(ThemeSettings())`, `_theme_from_kv({}) == theme_from_kv({})`, `DISPLAY_SETTINGS_ALLOWLIST == frozenset([*THEME_KV_KEYS, "dim_minutes_override"])`). Run: `uv run pytest tests/integration/test_sqlite_settings_repo.py -k share_seam -v` → Expected: FAIL (helpers do not exist or allowlist hard-coded).
- [ ] Implement: `ThemeDTO.from_domain`/`to_domain` keep field names but default missing theme via `THEME_DEFAULTS` (no shape change); `_theme_from_kv` delegates to `theme_from_kv`; `_theme_pairs` delegates to `theme_pairs`; `DISPLAY_SETTINGS_ALLOWLIST` built as `frozenset((*THEME_KV_KEYS, "dim_minutes_override"))`; `_check_theme_value` validates via `theme_choices(suffix)` membership (same error text). Keep `SettingsDTO`/`SqliteSettingsRepo.load/save` behavior byte-identical. Verify: `uv run pytest tests/integration/test_sqlite_settings_repo.py tests/contract/test_fixtures.py -q` → PASS. Depends on: Task 1.

### Task 3: Presentation plus script drift pinned and deduped

**Files:** `src/muhideen/views/display.py:153-174`, `src/muhideen/static/admin.js:12-45`, `src/muhideen/static/admin.js:117-153`, `src/muhideen/static/admin.js:291-335`, `tests/unit/test_admin_static.py`, `tests/unit/test_display_context.py`

**Goal:** Adding a knob touches `THEME_*` plus one render line and `DEFAULTS`; bodies never list knobs twice.

- [ ] Failing test: extend `tests/unit/test_admin_static.py` with `test_admin_js_defaults_match_theme_seam` (parse `DEFAULTS.theme` values, assert equals `THEME_DEFAULTS`; parse `DEFAULTS` top-level keys, assert `set(SettingsDTO.model_fields) <= keys` still holds) and `test_admin_js_bodies_share_defaults` (assert `settingsBodyFromDefaults` exists and both `JSON.parse(JSON.stringify(DEFAULTS))` call sites are gone, replaced by the helper). Add `test_body_class_matches_seam` to `tests/unit/test_display_context.py` (midnight/system/inline/12h/short/hide/compact theme renders `palette-midnight font-system density-compact`). Run: `uv run pytest tests/unit/test_admin_static.py tests/unit/test_display_context.py -q` → Expected: FAIL (helper missing, values unpinned).
- [ ] Implement: add `theme_css_class(theme: ThemeSettings) -> str` in `src/muhideen/core/values.py` returning `f"palette-{theme.palette} font-{theme.font} density-{theme.density}"`; `build_display_context` uses it (same string, `views` still imports `core` only). In `admin.js` add `function settingsBodyFromDefaults(overrides)` returning `Object.assign(JSON.parse(JSON.stringify(DEFAULTS)), overrides)` (keep `JSON` clone semantics); wizard body and save body call it with their field overrides instead of spelling every key twice; `DEFAULTS` values stay byte-identical. Verify: `uv run pytest tests/unit/test_admin_static.py tests/unit/test_display_context.py tests/e2e/test_admin_settings.py -q` → PASS. Depends on: Task 1.

### Task 4: Full gate

**Files:** all touched above

**Goal:** Prove no wire, fixture, or render drift.

- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `! rg -n "fastapi|httpx|datetime\\.now|time\\.time" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/` → 0 hits; `uv run pytest -q` → green with coverage ≥95; `rg -n "theme\\.palette.*classic-green|countdown_style.*boxes" src/muhideen/core/values.py src/muhideen/api/dto.py src/muhideen/adapters/sqlite_repo.py src/muhideen/static/admin.js` → defaults appear only as seam-table reads outside `core/values.py` (no second hard-coded default).
