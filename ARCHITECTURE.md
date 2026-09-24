# Muhideen Architecture

Inspired by paxman-python layering, adapted to a time-driven kiosk system. Single repo, single deployable, logical split enforced by tooling.

## Core Principles

### Future Fluidity Over Small Diffs
More work now to avoid compounded rework later. Solid core, abstracted processes, and testable dependency management ship on day 0. Never trade structural soundness for minimal scope or lowest immediate risk.

### Determinism Scoped to Clock + Snapshot
Given the same wall-clock time, the same stored schedule snapshot, and the same settings, the engine resolves the same prayer state and countdown targets. Stages are pure functions of `(now, schedule, settings)` — no hidden network, no ambient config, no display-side time math.

### Separation of Resolution and Presentation
The backend resolves *what* the state is (state, targets, freshness). The frontend renders *how* it looks. The display never computes prayer times, never picks the next prayer, never guesses dim windows. Themes receive read-only data via a narrow seam.

### Backend-First, Frontend-Independent
The API contract is the sole coupling. Backend lands contract + fixtures first; frontend builds against fixtures. Either side is replaceable (including a future Go appliance) without touching the other.

## Structural Layers

Dependencies flow inward. Outer layers depend on inner layers, never the reverse.

```
src/muhideen/
├── core/        # vocabulary, ports, errors — imports nothing from muhideen.*
├── domain/      # pure prayer logic — imports only core
├── engine/      # orchestration — imports core + domain
├── adapters/    # concrete ports — imports core (+ domain types)
├── api/         # HTTP handlers, DTOs — thin, imports engine/adapters/core
└── views/       # Jinja contexts, static assets — DTOs only, never domain/
```

### Core
Shared vocabulary and abstract ports: prayer/display/theme value objects (frozen) incl. marker vocabulary (`MarkerName`, `MarkerKind` + `marker_kind`), `PrayerRepo`, `SettingsRepo`, `DisplayRepo`, `UserRepo`, `JAKIMClient`, `CalcEngine`, `EventBus`, `MediaStore`, `Clock`, `TimeSyncProbe` (probe-of-system-state: reports whether the OS clock is NTP-synced, beside `Clock`'s wall time) ports, exception hierarchy (`MuhideenError`, `ContractError`, `ConfigError`, `SettingsNotInitializedError`, `ScheduleError`, `SyncError`). No framework, no SQLite, no HTTP.

### Domain
Pure functions over `(now, schedule, settings)`: state machine (§8 PRD), fallback chain, iqamah resolution, Hijri offset application, freshness flags. Frozen dataclasses (`slots=True`). Time arrives as an explicit `now` parameter — never read wall time directly — so tests pin time exactly (the `Clock` port itself is held by `engine/` and `adapters/`).

### Engine
Capability-agnostic orchestrator: load settings, resolve day schedule via fallback chain, compute next event, fan out SSE. Owns registry-free composition (no global mutable singletons except the single-writer DB handle created at startup).

### Adapters
One module per port, plus the migration runner. Landed: `sqlite_repo` (connection, single-writer `Database`, the prayer/settings/display/user repos, `VACUUM INTO` backup), `migrate`, `jakim_esolat` (defensive `period=year` client: pinned UA/timeout, in-client backoff, adapter-side naming map, parse + ordering rejection, keep-cache on fail), `calc_mabims` (MABIMS fallback behind `CalcEngine`, golden-tested against recorded JAKIM tables), `scheduler` (02:00 cron + 5m/15m/1h retries over APScheduler, injected `Clock`, started by the app lifespan under background wiring), `system_clock` (production `Clock`), `time_sync` (`SystemTimeSyncProbe` behind `TimeSyncProbe`: `timedatectl show` with `chronyc tracking` fallback, 30s TTL cache, fail-closed), `sse_bus` (queue-per-subscriber `EventBus` fan-out). `muhideen/service.py` (`muhideen` console script) and `muhideen/seed.py` (`muhideen-seed`), with repo-root `install.sh`/`update.sh` + `packaging/` driving device deployment, sit outside the layer contract like `views`/`migrations`. Pending with their ports: `cec`, and the MWL/ISNA/Egyptian calc methods (FR-1.3, contract-named only).

### API
FastAPI `def` sync handlers mapping HTTP ↔ engine. Pydantic DTOs are the executable contract. OpenAPI served LAN-only behind admin auth. No business logic here beyond parsing and status codes.

### Views + Themes
Jinja contexts will receive DTOs only (frontend slices 1B-1/1B-2). `themes/classic-green/` scaffold exists; hand-written `static/` assets land with the frontend. Community themes render in `<iframe sandbox="allow-scripts">` with CSP, fed read-only JSON via `postMessage`. Themes never import backend code.

## Ownership Boundaries

* Backend-owned: `core/`, `domain/`, `engine/`, `adapters/`, `api/`, `migrations/`.
* Frontend-owned: `views/display/`, `views/admin/`, `static/`, `themes/`, `api/fixtures/`.
* Cross-boundary change = contract change (`docs/api-contract.md` + fixtures + changelog). CI fails otherwise.

## Quality Enforcement

* **Strict pyright** on `src/` — no `type: ignore`.
* **Ruff** 88 cols — no `noqa` in `src/` (scoped per-file-ignores only).
* **Import-linter** layers (single configured contract, CI-enforced): `api → engine → adapters → domain → core`. Convention, not yet contracted: `views` may use `core` types only, nothing imports `views`, capabilities/themes never import each other.
* **Purity scans**: `domain/`, `engine/`, `core/` must not reference `fastapi`, `httpx`, `datetime.now`, `time.time`; `domain/` and `core/` must not reference `sqlite3` (`adapters/` is the sanctioned site); `views/` and `themes/` must not reference `domain/`; validation of prayer math never reads presentation flags. The textual scans run in CI (source-scan step) and as `! rg` steps in the contributor quality gate (`CONTRIBUTING.md`); the layer graph is enforced separately by import-linter.
* **Coverage** `fail_under=95`, branch mode. Every new state transition ships a pinned-time test.
