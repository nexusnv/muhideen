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
Shared vocabulary and abstract ports: prayer/display/theme value objects (frozen), `PrayerRepo`, `SettingsRepo`, `JAKIMClient`, `CalcEngine`, `EventBus`, `MediaStore`, `Clock` ports, exception hierarchy (`MuhideenError`, `ContractError`, `ScheduleError`, `SyncError`). No framework, no SQLite, no HTTP.

### Domain
Pure functions over `(now, schedule, settings)`: state machine (§8 PRD), fallback chain, iqamah resolution, Hijri offset application, freshness flags. Frozen dataclasses (`slots=True`). A `Clock` is injected — never read wall time directly — so tests pin time exactly.

### Engine
Capability-agnostic orchestrator: load settings, resolve day schedule via fallback chain, compute next event, fan out SSE. Owns registry-free composition (no global mutable singletons except the single-writer DB handle created at startup).

### Adapters
One module per port: `sqlite_repo`, `jakim_esolat`, `calc_mabims` (later MWL/ISNA/Egyptian), `system_clock`/`fake_clock`, `sse_bus`, `cec`. Data tables live beside logic (`adapters/data/`), never inside presentation.

### API
FastAPI `def` sync handlers mapping HTTP ↔ engine. Pydantic DTOs are the executable contract. OpenAPI served LAN-only behind admin auth. No business logic here beyond parsing and status codes.

### Views + Themes
Jinja templates receiving DTOs only. `static/app.css` / `static/app.js` hand-written. Community themes render in `<iframe sandbox="allow-scripts">` with CSP, fed read-only JSON via `postMessage`. Themes never import backend code.

## Ownership Boundaries

* Backend-owned: `core/`, `domain/`, `engine/`, `adapters/`, `api/`, `migrations/`.
* Frontend-owned: `views/display/`, `views/admin/`, `static/`, `themes/`, `api/fixtures/`.
* Cross-boundary change = contract change (`docs/api-contract.md` + fixtures + changelog). CI fails otherwise.

## Quality Enforcement

* **Strict pyright** on `src/` — no `type: ignore`.
* **Ruff** 88 cols — no `noqa` in `src/` (scoped per-file-ignores only).
* **Import-linter** layers: `api → engine → adapters → domain → core`; `views` may use `core` types only; nothing imports `views`; capabilities/themes never import each other.
* **Purity scans**: `domain/` must not reference `fastapi`, `sqlite3`, `httpx`, `datetime.now`, `time.time`; `views/` and `themes/` must not reference `domain/`; validation of prayer math never reads presentation flags. CI source-scan enforced.
* **Coverage** `fail_under=95`, branch mode. Every new state transition ships a pinned-time test.
