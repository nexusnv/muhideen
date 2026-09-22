# Slice 1A-1 Core Vocab + Ports Implementation Plan

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`.

**Goal:** Land `src/muhideen/core/` — frozen value objects, abstract ports, and error hierarchy — with unit tests proving immutability, equality, hashability, and protocol compliance.

**Architecture:** Innermost layer only. `core/` imports stdlib (`dataclasses`, `datetime`, `enum`, `typing`) and nothing from `muhideen.*`. No framework, no SQLite, no HTTP, no wall-clock reads. Time fields use `datetime.time` / `datetime.datetime` (tz-aware) so ordering logic in 1A-2 never parses strings; string parsing stays in adapters (1A-5/1A-6) and DTOs (1A-3).

**Tech Stack:** Python 3.11+, uv, ruff, strict pyright, import-linter, pytest (`unit` marker only — contract/integration belong to later slices).

**References:** `docs/development/PHASES_AND_SLICES.md` slice 1A-1 row, `ARCHITECTURE.md` Core section + purity scans, `CONTEXT.md` (term names: Prayer State, Fallback Chain, Stale, Iqamah, Zone, Contract), `PRD.md` §3.1/§6.2/§8 (field sets, state names), `docs/api-contract.md` (field names DTOs will mirror), `docs/adr/0003-sqlite-behind-ports.md` (port list), `src/muhideen/core/__init__.py:1` (empty).

**Branch:** `feature/1a-1-core-vocab-ports` (from `main`)

---

## File Structure

- Create: `src/muhideen/core/values.py` — `PrayerName`, `PrayerState`, `ScheduleSource` enums; `PrayerDay`, `NextEvent`, `IqamahRule`, `Settings` frozen dataclasses (`slots=True`)
- Create: `src/muhideen/core/ports.py` — `PrayerRepo`, `SettingsRepo`, `JAKIMClient`, `CalcEngine`, `Clock`, `EventBus`, `MediaStore` runtime-checkable `Protocol`s
- Create: `src/muhideen/core/errors.py` — `MuhideenError` base + `ContractError`, `ScheduleError`, `SyncError`, `ConfigError`
- Modify: `src/muhideen/core/__init__.py` — re-export public names
- Test: `tests/unit/test_core_values.py` — immutability, equality, hashability, enum completeness
- Test: `tests/unit/test_core_ports.py` — protocol compliance (satisfying vs incomplete classes)
- Test: `tests/unit/test_core_errors.py` — hierarchy + carried fields

No `domain/`, `engine/`, `adapters/`, `api/`, `views/` changes. No fixture changes.

---

### Task 1: Value objects — failing guards

**Files:** `tests/unit/test_core_values.py` (create)

**Goal:** Prove the required shapes with red tests before `values.py` exists.

- [ ] Add failing tests: frozen-assign raises `FrozenInstanceError` for each dataclass; equal fields compare equal / differ not; all hashable; `PrayerState` has exactly `NORMAL/PRE_ADHAN/ADHAN/IQAMAH_COUNTDOWN/SALAH_DIM`; `PrayerName` covers `fajr/syuruq/dhuhr/asr/maghrib/isha/jumuah`; `ScheduleSource` covers `jakim/calc/manual`. Run: `uv run pytest tests/unit/test_core_values.py -v` → Expected: FAIL (ImportError, no module).

### Task 2: Value objects — minimal implementation

**Files:** `src/muhideen/core/values.py` (create), `src/muhideen/core/__init__.py` (modify)

**Goal:** Green unit tests with exact field sets below. No validation logic beyond type shape (ordering/fallback rules are 1A-2).

- [ ] `PrayerDay`: `date: date`, `zone: str`, `fajr/syuruq/dhuhr/asr/maghrib/isha: time`, `source: ScheduleSource`, `fetched_at: datetime`. `NextEvent`: `now: datetime`, `state: PrayerState`, `next_prayer: PrayerName | None`, `adhan_at/iqamah_at/dim_until: datetime | None`, `stale: bool`. `IqamahRule`: `prayer: PrayerName`, `mode: Literal["delay","fixed"]`, `delay_minutes: int = 10`, `fixed_time: time | None = None`. `Settings`: `masjid_name: str`, `zone: str`, `hijri_offset: int` (-2..2 enforced in `__post_init__`), `adhan_duration_s: int = 180`, `dim_minutes_default: int = 20`, `dim_minutes_jumuah: int = 45`. All `@dataclass(frozen=True, slots=True)`. Verify: `uv run pytest tests/unit/test_core_values.py -v` → PASS; `uv run pytest -m unit -q` → PASS.

### Task 3: Ports — failing guards then protocols

**Files:** `tests/unit/test_core_ports.py` (create), `src/muhideen/core/ports.py` (create)

**Goal:** Seven runtime-checkable protocols with the minimal method surface 1A-4+ needs.

- [ ] Failing tests first: a stub satisfying each protocol passes `isinstance`; a stub missing one method fails. Method surface: `PrayerRepo.get_day(date, zone)` / `save_day(PrayerDay)`; `SettingsRepo.load()` / `save(Settings)`; `JAKIMClient.fetch_week(zone)`; `CalcEngine.compute_day(date, lat, lon, method)`; `Clock.now()` / `monotonic()`; `EventBus.publish(event)`; `MediaStore.list_enabled()`. All `typing.Protocol` + `@runtime_checkable`, methods raising `NotImplementedError` via `...` bodies. Verify: tests → PASS; `uv run pyright` → clean (no `type: ignore`).
- [ ] Regression: `uv run pytest -m unit -q` → PASS.

### Task 4: Errors + gate

**Files:** `src/muhideen/core/errors.py` (create), `tests/unit/test_core_errors.py` (create)

**Goal:** Typed failure modes; `ScheduleError` and `SyncError` carry the offending `zone`/`date` for stale-banner diagnostics.

- [ ] `MuhideenError(Exception)` base; `ContractError`, `ScheduleError`, `SyncError`, `ConfigError` subclass it. `ScheduleError`/`SyncError` take `(message, zone="", date="")` and expose both attrs. Tests: hierarchy isinstance checks + attr preservation. Verify: `uv run pytest -m unit -q` → PASS.
- [ ] Final gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → all green. Purity spot-check: `rg -n "fastapi|sqlite3|httpx|datetime\.now|time\.time" src/muhideen/core/` → 0 hits.

---

## Out of Scope (later slices)

- Transition/fallback/iqamah computation (1A-2), DTOs + fixture parity (1A-3), engine orchestration (1A-4), SQLite/JAKIM/calc adapters (1A-5/1A-6), HTTP handlers (1A-7), service/installer (1A-8), any `views/` or theme work (1B).
