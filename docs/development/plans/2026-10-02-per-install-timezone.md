# Per-installation timezone (device clock follows settings) — Implementation Plan (issue #48)

> **For workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: plan self-review below, `oracle` on the branch diff before PR handoff. Do NOT commit this plan file until the slice is approved for implementation.

**Goal:** A mosque outside Malaysia can run correct local times via a validated IANA `timezone` setting that flows from setup/settings UI into the service clock, scheduler, and calc engine, defaulting to `Asia/Kuala_Lumpur` so existing installs are unaffected.

**Architecture:** Add `timezone: str` to the `Settings` value object (validated via `ZoneInfo`, default KL) and `SettingsDTO` (additive default so old wizard bodies still validate); persist as the `timezone` settings-table key with KL fallback for legacy rows; `create_production_app` migrates then loads settings to pick the clock tz (falling back to its `tz` param when unconfigured/corrupt); settings change takes effect on service restart (documented, no live clock rebuild); admin setup/settings + `admin.js` carry the field; `muhideen-seed` gains `--timezone` for first boot and `install.sh` passes it through.

**Tech Stack:** Python 3.11+, `zoneinfo`, FastAPI/Pydantic DTOs, SQLite kv settings, Jinja admin templates + `admin.js`, pytest markers `unit/e2e`.

**References:** Issue #48 (split from #36; methods/Asr/wizard parity already landed); `src/muhideen/core/values.py:289-312` (`Settings`); `src/muhideen/api/dto.py:362-442` (`SettingsDTO`); `src/muhideen/adapters/sqlite_repo.py:228-304` (load/save); `src/muhideen/api/app.py:112,1378-1400` (`_PROD_TZ`, `create_production_app`); `src/muhideen/seed.py:29-83` (first boot + clock); `src/muhideen/service.py:33-37` (serve entry); `src/muhideen/adapters/system_clock.py:10-19` (`SystemClock`); `src/muhideen/views/templates/admin/setup.html:26-38` (wizard step 2); `src/muhideen/views/templates/admin/settings.html:73-106` (time section); `src/muhideen/static/admin.js:14-32,126-132,312-323` (wizard defaults/body, save body); `api/fixtures/settings.json` (settings sample); `docs/api-contract.md:70-114` (settings docs); `install.sh:25-140` (seed passthrough).

**Branch:** `feature/per-install-timezone` (cut when approved; base on `main` — independent of other open issues)

---

## Background the implementer needs

### Field naming (locked)

`timezone` (IANA name, e.g. `Asia/Kuala_Lumpur`, `Europe/London`) on `Settings` + `SettingsDTO` + settings-table key. The existing `zone` field (JAKIM zone code like `SGR01`) is untouched — the two names coexist: `zone` = timetable zone, `timezone` = device clock zone. Admin labels must read "Timezone (IANA)" to avoid confusion.

### Design decisions (locked)

1. Validation at the VO boundary: `Settings.__post_init__` tries `ZoneInfo(self.timezone)` and raises `ValueError` on `ZoneInfoNotFoundError`/empty, so bad values surface as 422/`ConfigError` through the existing translation layers. No new error type.
2. Additive DTO default: `timezone: str = "Asia/Kuala_Lumpur"` on `SettingsDTO` (same precedent as `asr_juristic`/`countdown_*`/`theme` defaults) so older wizard bodies still validate; `to_domain`/`from_domain` map it 1:1.
3. Legacy rows fall back to KL: `SqliteSettingsRepo.load` uses `kv.get("timezone", "Asia/Kuala_Lumpur")`; `save` always writes the key. No migration needed (kv table, missing key = default). The seeded-defaults test must pin both sides.
4. Clock is build-once at startup: `create_production_app` migrates the DB, then best-effort loads settings to pick the tz (`settings.timezone` when configured, else the `tz` param default KL on `SettingsNotInitializedError`/`ConfigError`/bad value with a logged warning). A `PUT /api/settings` timezone change takes effect on service restart — stated in the admin hint text and the plan, no live rebuild of `SystemClock`/scheduler/calc (out of scope, would require re-creating the engine + rescheduling cron triggers mid-process).
5. Seed/install: `muhideen-seed` gains `--timezone` (default KL); first-boot `Settings(...)` uses it; configured installs ignore a differing `--timezone` with the same logged-notice pattern as `--zone`. `install.sh` gains `--timezone` passthrough to seed. The sync clock itself stays KL in seed (sync is date-based, tz-independent) — only the stored setting matters.
6. Non-goals: live clock rebuild without restart, per-display timezones (single device clock), DST edge-case handling beyond `zoneinfo` (stdlib owns it), `pre_note`/banner text changes, `locales` changes.

## File Structure

- Modify first: `src/muhideen/core/values.py` — `timezone` field + `ZoneInfo` guard on `Settings`
- Modify: `src/muhideen/api/dto.py` — `timezone` on `SettingsDTO` + mappers
- Modify: `api/fixtures/settings.json` — add `"timezone": "Asia/Kuala_Lumpur"` (keep key order alphabetical-ish as-is: insert after `theme`? file is flat alphabetical — `timezone` sorts after `theme`; mirror that)
- Modify: `docs/api-contract.md` — settings sections mention `timezone` (IANA, default KL, restart-required)
- Modify: `src/muhideen/adapters/sqlite_repo.py` — load fallback + save key
- Modify: `src/muhideen/api/app.py` — `create_production_app` reads settings tz after migrate (keep `tz` param as fallback/override)
- Modify: `src/muhideen/seed.py` — `--timezone` flag + first-boot use
- Modify: `install.sh` — `--timezone` passthrough
- Modify: `src/muhideen/views/templates/admin/setup.html` — wizard timezone input (step 2, default `Asia/Kuala_Lumpur`)
- Modify: `src/muhideen/views/templates/admin/settings.html` — timezone row in Time section + restart hint
- Modify: `src/muhideen/static/admin.js` — wizard defaults/body + save body carry `timezone`
- Test: `tests/unit/test_settings.py` (or wherever Settings guards live — grep `hijri_offset out of range` to find) — validation + default
- Test: settings repo tests (grep `zone_code` in `tests/` to find) — round-trip + legacy fallback
- Test: DTO/contract tests (grep `settings.json` in `tests/` to find) — fixture ↔ DTO parity incl. timezone
- Test: e2e/composition — non-MY zone end-to-end (`Europe/London` clock `data-tz` on `/display` or equivalent composition pin)
- Docs: `CHANGELOG.md` Added entry (additive `timezone`, default KL, restart-required, no contract break)

No migration, no locale change, no DTO removal, no `setup.html` step-count change (input joins existing step 2).

---

### Task 1: Settings VO + DTO + fixtures/contract

**Files:** `src/muhideen/core/values.py:289-319`, `src/muhideen/api/dto.py:362-442`, `api/fixtures/settings.json`, `docs/api-contract.md:70-136`

**Goal:** The `timezone` value exists, validates, and survives the wire. Runs first — repo/composition/admin all read from these owners.

- [ ] **Step 1: Write the failing Settings validation test**

In the Settings-guard test file (find via `grep -rn "hijri_offset out of range" tests/`), add:

```python
def test_timezone_defaults_to_kl_and_rejects_unknown() -> None:
    from muhideen.core.values import Settings

    assert (
        Settings(masjid_name="M", zone="SGR01", hijri_offset=0).timezone
        == "Asia/Kuala_Lumpur"
    )
    import pytest

    with pytest.raises(ValueError, match="unknown timezone"):
        Settings(masjid_name="M", zone="SGR01", hijri_offset=0, timezone="Mars/Olympus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_values.py -q -k timezone` (replace with the real file found by grep)
Expected: FAIL (`TypeError: unexpected keyword argument 'timezone'` or `assert` on missing attr).

- [ ] **Step 3: Minimal Settings implementation**

```python
# values.py top: from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
TIMEZONE_DEFAULT = "Asia/Kuala_Lumpur"
# Settings field (after zone/hijri_offset block, default KL):
timezone: str = TIMEZONE_DEFAULT
# __post_init__ guard (next to asr_juristic check):
try:
    ZoneInfo(self.timezone)
except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
    raise ValueError(f"unknown timezone: {self.timezone!r}") from exc
```

Keep field order stable (append after `theme` to avoid positional-arg churn — `Settings` is keyword-built everywhere; verify with `grep -rn "Settings(" src/ | head`).

- [ ] **Step 4: DTO + fixture + contract**

```python
# dto.py SettingsDTO (after zone/hijri_offset, additive default):
timezone: str = "Asia/Kuala_Lumpur"
# from_domain: timezone=settings.timezone
# to_domain: timezone=self.timezone
```

`api/fixtures/settings.json`: add `"timezone": "Asia/Kuala_Lumpur"` (after `"theme"` object, comma-correct JSON).
`docs/api-contract.md` GET + PUT settings sections: mention `timezone` (IANA string, default `Asia/Kuala_Lumpur`, restart-required to take effect).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q -k "timezone or settings or dto or contract"` (then full `tests/unit -q` if green)
Expected: PASS. Contract/fixture parity tests (find via `grep -rln "settings.json" tests/`) must pass — if the contract test enumerates DTO fields explicitly, update its expected sample too.

- [ ] **Step 6: Lint the touched files**

Run: `uv run ruff check src/muhideen/core/values.py src/muhideen/api/dto.py && uv run ruff format --check src/muhideen/core/values.py src/muhideen/api/dto.py`
Expected: clean (run `ruff format` on them if needed).

### Task 2: Repo persistence (load fallback + save)

**Files:** `src/muhideen/adapters/sqlite_repo.py:228-304`

**Goal:** Stored timezone round-trips; legacy DBs without the key behave as KL.

- [ ] **Step 1: Write the failing repo tests**

In the settings-repo test file (find via `grep -rln "zone_code" tests/`), add:

```python
def test_settings_timezone_round_trip(tmp_repo) -> None:
    # save Settings(timezone="Europe/London"), load, assert == "Europe/London"
def test_settings_timezone_defaults_for_legacy_rows(tmp_repo) -> None:
    # save default Settings, delete timezone row (or insert legacy kv set without it), load, assert == "Asia/Kuala_Lumpur"
```

Use the file's existing tmp-repo fixture name (read the top of the file first — do not invent `tmp_repo` if the fixture is called `db`/`repo`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest <repo-test-file> -q -k timezone`
Expected: FAIL (`TypeError` on `Settings(timezone=...)` if Task 1 regressed, else loaded value `!= "Europe/London"` / missing-key `KeyError`).

- [ ] **Step 3: Minimal repo implementation**

```python
# load(): timezone=kv.get("timezone", "Asia/Kuala_Lumpur"),
# save(): ("timezone", settings.timezone),
```

Place `timezone` pair next to `("zone_code", ...)` for readability. Load relies on the VO guard to reject corrupt stored values as `ConfigError` (existing `except (ValueError, KeyError)` covers it — verify no new except needed).

- [ ] **Step 4: Run tests**

Run: `uv run pytest <repo-test-file> tests/unit -q`
Expected: PASS, including the seeded-defaults test if present (`grep -rn "seeded" tests/ | head` — update its expected kv set with `timezone` if it enumerates keys).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/muhideen/adapters/sqlite_repo.py && uv run ruff format --check src/muhideen/adapters/sqlite_repo.py`

### Task 3: Production wiring (app + seed + install.sh)

**Files:** `src/muhideen/api/app.py:1378-1400`, `src/muhideen/seed.py:29-83`, `install.sh:25-140`

**Goal:** The device clock follows the stored setting; first boot can set it; existing installs keep KL.

- [ ] **Step 1: Write the failing composition test**

Add (in the app-composition test file — find via `grep -rln "create_production_app" tests/`; if none exists, put it in the new/existing e2e settings test):

```python
def test_production_clock_follows_settings_timezone(tmp_path) -> None:
    # build DB: migrate + save Settings(timezone="Europe/London")
    # app = create_production_app(db_path)  (run_background=False)
    # GET /display?id=... or read composition clock tz key == "Europe/London"
```

Simplest stable pin: use the test-suite `surface`/`client` fixtures if present (see `tests/e2e/test_display.py` header); otherwise call `create_production_app` directly and assert the rendered `/display` HTML contains `data-tz="Europe/London"`. Default-behavior pin: unconfigured DB → clock is `Asia/Kuala_Lumpur` (existing tests already assume KL — keep them green).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest <chosen-file> -q -k timezone`
Expected: FAIL (no `data-tz="Europe/London"` — clock is hardcoded KL).

- [ ] **Step 3: Minimal app implementation**

```python
def create_production_app(db_path, *, tz=_PROD_TZ, run_background=True):
    database = Database(db_path)
    migrate(
        database
    )  # move migrate BEFORE clock construction (currently inside lifespan — keep lifespan migrate too, idempotent)
    try:
        stored_tz = SqliteSettingsRepo(database).load().timezone
        clock_tz = ZoneInfo(stored_tz)
    except Exception:  # SettingsNotInitializedError | ConfigError | ZoneInfo error
        logger.warning("using fallback timezone %s", tz)
        clock_tz = tz
    clock = SystemClock(clock_tz)
    ...
```

Notes: `migrate` is idempotent (existing lifespan call stays — double-migrate is safe; verify by reading `adapters/migrate.py` header first). Catch narrowly: `(SettingsNotInitializedError, ConfigError, ValueError, ZoneInfoNotFoundError)` — import what the file doesn't already have. Log at warning with the fallback value. Do NOT change the `tz` param signature (tests/back-compat may pass explicit tz).

- [ ] **Step 4: Seed + install.sh**

```python
# seed.py: parser.add_argument("--timezone", default="Asia/Kuala_Lumpur", help="IANA timezone (first boot only)")
# first boot: Settings(..., timezone=args.timezone)  (validate → exit 2 on ValueError via existing ConfigError path? Settings raises ValueError — wrap: try/except ValueError → print + return 2, mirroring invalid-settings branch)
# configured: if args.timezone != settings.timezone: print notice (same pattern as --zone), never overwrite
```

`install.sh`: add `--timezone` flag (default empty → omit), passthrough: `if [[ -n "$TIMEZONE" ]]; then seed_args+=(--timezone "$TIMEZONE"); fi`, usage line update. Keep `--zone` behavior byte-identical.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/e2e/test_display.py tests/unit -q`
Expected: PASS (KL-default tests unaffected; new London pin green).

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/muhideen/api/app.py src/muhideen/seed.py && uv run ruff format --check src/muhideen/api/app.py src/muhideen/seed.py` (+ `bash -n install.sh`).

### Task 4: Admin UI (setup wizard + settings + admin.js)

**Files:** `src/muhideen/views/templates/admin/setup.html:26-38`, `src/muhideen/views/templates/admin/settings.html:73-106`, `src/muhideen/static/admin.js:14-32,126-132,312-323`

**Goal:** An admin can set timezone at first boot and change it later (restart-required noted).

- [ ] **Step 1: Write the failing UI-carried test**

Admin HTML is thin-client — pin the wire, not the DOM: extend the settings-API test (find via `grep -rln "PUT /api/settings\|put_settings\|/api/settings" tests/`) with:

```python
def test_settings_api_round_trips_timezone(admin_client) -> None:
    # PUT {"timezone": "Europe/London", ...full body...} → 200, GET → "Europe/London"
    # PUT {"timezone": "Mars/Olympus", ...} → 422
```

Use the file's existing authed-client + full-body helpers (read them first — PUT is full-replace, so copy the existing valid body and change only `timezone`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest <settings-api-test> -q -k timezone`
Expected: FAIL (422 `extra=forbid`? No — with Task 1 done DTO accepts it; if Task 1 landed, this passes already — then the test still pins the behavior; note that in the commit message).

- [ ] **Step 3: Template + JS implementation**

`setup.html` step 2 (after method/coords block): `<label class="field"><span class="lbl">Timezone (IANA, e.g. Asia/Kuala_Lumpur)</span><input id="w-timezone" type="text" placeholder="Asia/Kuala_Lumpur" value="Asia/Kuala_Lumpur" style="font-family:var(--mono);"></label>` + hint `Takes effect immediately for new installs.`
`settings.html` Time section (after zone row `:66-69`): timezone row mirroring it + `<p class="hint">Changing timezone requires a service restart to take effect.</p>`.
`admin.js`: wizard defaults add `"timezone": "Asia/Kuala_Lumpur"`; wizard body add `timezone: document.getElementById("w-timezone").value || "Asia/Kuala_Lumpur"`; save body add `timezone: document.getElementById("s-timezone").value`; wizard step-2 validation: non-empty (timezone validity is server-422, same as zone).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/e2e -q -k "settings or setup or admin"` then `uv run pytest -q` (full) if green.
Expected: PASS. No `locales/en.json` change (timezone is a value, not a translatable string — verify honesty test still passes: `uv run pytest tests/unit/test_locales.py -q`).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/muhideen/static/admin.js 2>/dev/null || true` (JS — ruff may not cover it; at minimum `node --check` if node exists, else visual brace-match). Python files untouched here.

### Task 5: Changelog + full gate

- [ ] **Step 1: CHANGELOG `[Unreleased] Added`**: one bullet — per-installation `timezone` (IANA, default `Asia/Kuala_Lumpur`, existing installs unaffected; setup wizard + settings UI; `PUT` full-replace additive; service restart required; fixtures + contract updated). No dev-plan filename reference (per `docs/development/AGENTS.md` hard guidance).
- [ ] **Step 2: Gate**: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #48 timezone half → Tasks 1–4 (VO validation, DTO/wire, repo persistence, startup clock, seed/install first-boot, wizard + settings UI, non-MY e2e pin, default-KL unaffected); methods/Asr/wizard-method parity explicitly out (already landed per CHANGELOG); live rebuild, per-display tz, DST-beyond-stdlib explicitly excluded with restart-required pointer.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task — files, tests, commands all named (grep-first steps name the fallback when the exact test file is located by grep rather than assumed).
3. Type consistency: `timezone: str` everywhere (VO, DTO, kv string, JSON string, HTML/JS string); `ZoneInfo` only at validation + clock construction; `TIMEZONE_DEFAULT`/`_PROD_TZ` both `Asia/Kuala_Lumpur`.
4. Momus dry gate: `values.py:289-319` / `dto.py:362-442` / `sqlite_repo.py:228-304` / `app.py:112,1378-1400` / `seed.py:29-83` / `service.py:33-37` / `setup.html:26-38` / `settings.html:73-106` / `admin.js:14-32,126-132,312-323` / `settings.json` / `api-contract.md:70-136` / `install.sh:25-140` references verified on disk this session; task order fixed (VO/DTO before repo before wiring before UI so each pin can pass); each task has a red→green test + exact `uv run` command. `locales/*.json` untouched.
