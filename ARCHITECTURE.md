# Muhideen Architecture

Inspired by paxman-python layering, adapted to a time-driven kiosk system. Single repo, single deployable, logical split enforced by tooling.

This document is the contributor's map: what the system looks like whole, why it looks that way, how the parts relate, and where new work plugs in. Requirements live in `PRD.md`, vocabulary in `CONTEXT.md`, the wire shapes in `docs/api-contract.md` + `api/fixtures/`, lasting decisions in `docs/adr/`, and test organization in `TESTING_STRATEGY.md`.

## System Overview (As Built, Plus Planned)

One device serves two audiences from one backend. The backend is landed (Phase 1A); the frontend is planned (Phase 1B+) and builds against the frozen contract.

```
External world                    The device (one deployable)
================                  ==========================================

JAKIM e-solat tables ──sync──▶
                               ┌─ BACKEND (landed) ──────────────────────┐
NTP / chrony ──health──▶       │ resolve prayer state + serve it as JSON │
                               │ engine · adapters · api · persistence   │
Admin phone/PC ──LAN──▶        └─────────────────┬───────────────────────┘
    (setup, settings, themes,                        │ the contract is the
     media, backup)                                  │ only coupling
                                                     ▼
                               ┌─ FRONTEND (planned) ────────────────────┐
Jama'ah ──reads──▶             │ render resolved state, never compute it │
                               │ display page · admin pages · themes     │
Chromium kiosk ──pull──▶       └─────────────────────────────────────────┘
```

Backend and frontend are separated by exactly one seam: the versioned JSON contract (`docs/api-contract.md`, executable as Pydantic DTOs, exemplified by `api/fixtures/`). The backend resolves *what* the state is (state, countdown targets, freshness); the frontend renders *how* it looks. The display never computes prayer times, never picks the next prayer, never guesses dim windows. Themes receive read-only data through a narrow seam and can never reach admin functions.

Planned-but-not-yet-built pieces all sit above or beside that seam without moving it: the display and admin pages, the carousel manager, the theme pipeline, backup/restore UI, audio upload, display groups, CEC power control, and extra calculation methods (MWL/ISNA/Egyptian are named in the contract today; only MABIMS computes). None of them require contract breakage — the contract grows by additive fields.

## Why This Architecture

Each structural choice answers a constraint of the problem: an offline-first kiosk, maintained by volunteers, running on a Raspberry Pi.

**Ports-and-adapters (hexagonal) core.** Every external capability — schedule fetching, calculation, storage, time, event fan-out — is consumed through an abstract port defined in `core/`, with the real implementation injected from `adapters/`. This buys two things: testability (the engine is exercised against in-memory fakes with pinned time, no DB or network) and replaceability (a calculation library, an HTTP client, or the whole backend language can be swapped without touching prayer logic). The prayer math never knows what fetched it or what renders it.

**Single repo, single deployable.** The Pi installs one artifact and updates one version. A split frontend/backend repo would buy independent versioning at the cost of version skew on a device with no operator — the wrong trade for an appliance. Parallel work still happens: the contract plus fixtures decouple the contributor tracks instead of repository boundaries.

**Contract-first, backend-first.** The backend lands DTOs, fixtures, and OpenAPI before any UI exists, and CI fails on drift between them. Frontend contributors then build against `api/fixtures/` served by a dependency-free mock, with no database, no JAKIM access, and no hardware. Either side is replaceable behind the frozen contract — including the long-term option of a compiled appliance binary.

**Synchronous server, single worker, single writer.** SQLite is safe under exactly one writer, so the server runs sync handlers on one worker and funnels every database access through one locked connection. No async database code exists anywhere, which removes an entire class of concurrency bugs on a device nobody babysits.

**Two clocks, two jobs.** Wall-clock time answers *which* prayer event is next; monotonic time answers *how long* the countdown is. Countdowns therefore stay arithmetically honest across NTP steps and manual clock changes, while a separate health signal reports whether the wall clock itself can be trusted.

### Core Principles

#### Future Fluidity Over Small Diffs
More work now to avoid compounded rework later. Solid core, abstracted processes, and testable dependency management ship on day 0. Never trade structural soundness for minimal scope or lowest immediate risk.

#### Determinism Scoped to Clock + Snapshot
Given the same wall-clock time, the same stored schedule snapshot, and the same settings, the engine resolves the same prayer state and countdown targets. Stages are pure functions of `(now, schedule, settings)` — no hidden network, no ambient config, no display-side time math.

#### Separation of Resolution and Presentation
The backend resolves *what* the state is (state, targets, freshness). The frontend renders *how* it looks. The display never computes prayer times, never picks the next prayer, never guesses dim windows. Themes receive read-only data via a narrow seam.

#### Backend-First, Frontend-Independent
The API contract is the sole coupling. Backend lands contract + fixtures first; frontend builds against fixtures. Either side is replaceable (including a future Go appliance) without touching the other.

## Relationships Between the Parts

### Within the backend: layers flow inward

Dependencies point inward. Outer layers depend on inner layers, never the reverse — enforced by import-linter in CI, not by convention.

```
src/muhideen/
├── core/        # vocabulary, ports, errors — imports nothing from muhideen.*
├── domain/      # pure prayer logic — imports only core
├── engine/      # orchestration — imports core + domain
├── adapters/    # concrete ports — imports core (+ domain types)
├── api/         # HTTP handlers, DTOs — thin, imports engine/adapters/core
└── views/       # presentation contexts — DTOs only, never domain/
```

A request walks the layers in one direction. In prose: an HTTP handler parses and validates input, the engine loads settings and resolves the day's schedule through the fallback chain, pure domain functions compute the display state for the pinned instant, and the result is mapped to a DTO and returned. Error mapping happens at the boundary only (unknown schedule → 404, unconfigured installation → 503, invalid input → 422). There is deliberately no business logic in the API layer beyond parsing and status codes, and no framework, SQL, or HTTP types in the inner layers.

Background work follows the same layering. A one-second ticker recomputes the current event and publishes `state` when anything the client renders changed, plus a per-minute `tick`; a daily scheduler refreshes the stored year table with retries. Both are composed at startup (the app lifespan) and both drive the same engine use-cases the request path uses — there is no second code path for background computation.

### Backend to frontend: one narrow seam

The frontend relates to the backend exclusively as a consumer of resolved data:

- **Poll the state, stream the changes.** Clients render from `next-event` and stay fresh via the SSE event stream (`state` on transitions, `tick` each minute, `config-update` when settings change), with a 60-second poll as fallback. The client ticks its visible countdown locally between server updates and resyncs on every event.
- **Identify, don't compute.** Each display is a dumb client with a stable registration ID, heartbeating every 30 seconds. The server records presence in batches; the client never derives anything from it.
- **Admin is a thin client too.** The setup wizard and later settings screens speak the same settings API — full-replace read/write with live reload fanning out as `config-update`. There is no wizard-only code path to drift from the main one.
- **Fixtures are the frontend's backend.** Until the frontend lands, and after every contract change, `api/fixtures/` plus the mock server are the build target. If it is not in fixtures and the contract document, the frontend cannot rely on it.

### Internal to external: dependencies and users

External dependencies are kept at arm's length behind ports, and each has a stated degradation story:

| External | Relationship | When it fails |
|---|---|---|
| JAKIM e-solat (unofficial tables) | One polite daily fetch per zone; parsed defensively, never trusted | Keep cache; fall through the offline chain; admin can pin calc-only mode |
| NTP / chrony | Read-only health probe with caching; never sets the clock | `time_synced: false` banner; countdowns continue on the monotonic clock |
| System clock + monotonic clock | Sole time sources, injected everywhere | Pinned fakes in tests; drift latch if wall and monotonic disagree |
| SQLite (stdlib) | Single-file store behind repository ports | Short transactions + WAL; backups before updates |
| Calculation library | Pure math behind the calc port, golden-tested | Tolerance-pinned outputs; swap caught by golden tests |
| Chromium kiosk | Pull-only display client | Poll fallback covers stream loss; no server-side display state |
| Avahi/mDNS, systemd | Discovery and supervision | Direct-IP fallback; service restarts on failure |

And the three users from the domain glossary each touch a different surface: the **jama'ah** reads only the public display; the **admin** configures over LAN from a phone or PC and never edits files; the **integrator** installs hardware and extends the system through the seams described below.

## How Prayer Times Are Derived

Prayer times reach the screen through three stages: acquire candidate schedules, resolve one day, compute the display state. Exactly one zone is configured per installation, and every stage respects that.

**Acquire.** The scheduler fetches the whole calendar year for the configured zone once daily (with short retries on failure), storing each day with its provenance. Independently, the on-device calculator can derive a day's markers from coordinates plus the pinned MABIMS parameters. Each stored day carries its source, and each fetch either fully validates or leaves the cache untouched. At resolve time markers merge per marker with cached (API) markers winning over calc; rows are complete today so a present cached day wins wholesale.

**Resolve (the fallback chain).** For a requested date, the engine takes the first candidate that matches, in fixed priority:

```
1. cached day for (date, zone)      → fresh if recent and first-party
2. calculated day for (date, zone)  → always flagged as fallback provenance
3. last-known saved day for zone    → always flagged stale
4. none of the above                → unknown schedule (404 at the API)
```

Per-marker precedence: when both candidates exist, each marker takes the cached value; provenance follows the cached day. Calc derivation itself is per marker: 6 from the library plus `imsak_offset_min`/`dhuha_offset_min` offsets (0 hides imsak).

Freshness is a separate flag from identity: anything older than 48 hours or produced by a degraded step renders with a banner, never silently. A requested zone that is not the configured zone is unknown even when coordinates exist — the system never serves a schedule stamped for a zone it was not resolved for. An installation with no settings yet reports itself unconfigured rather than guessing.

**Compute (the state machine).** Given the resolved day, neighboring-day context for midnight crossover, the iqamah rules, and settings, pure functions determine the single current state — `NORMAL`, `PRE_ADHAN`, `ADHAN`, `IQAMAH_COUNTDOWN`, or `SALAH_DIM` — plus the countdown targets. Only Prayer Time Markers (the five prayers, with Jumuah replacing Dhuhr on Friday) can enter a non-normal state; Boundary Time Markers (Imsak, Syuruq, Dhuha) contribute at most an opt-in informational pointer that never influences the state. Iqamah per prayer is either minutes-after-adhan or a fixed clock time, and dimming lasts a configured number of minutes after iqamah (longer for Jumuah). The Hijri date shown alongside is a stored calendar value shifted by the configured regional offset, not computed prayer math.

## Persistence Layer

One SQLite file holds all device state, accessed through exactly one locked connection. The layer's job is durability and atomicity, never interpretation: repositories round-trip rows to value objects, while ordering, freshness, and validation live in the domain and at the load boundary.

- **Single writer.** Every read, write, migration, and backup passes through one connection guarded by one lock, so the multi-threaded request pool cannot interleave writes. Transactions are kept short; a crash between statements cannot leave half a settings write.
- **Migrations, not an ORM.** Schema versions are numbered SQL files applied in order at boot, tracked by an integer version stamp. Each migration is idempotent, each rolls back, and there is no migration framework to install on the device — a deliberate consequence of the zero-admin constraint.
- **What lives where.** Installation identity and tuning (settings keys, iqamah rules) in one group; the schedule cache (one row per date and zone, with source and fetch time) in another; presence (display registrations, batched heartbeats) in a third; credentials (one admin row with a salted hash) in a fourth; media and grouping tables stand ready for the content slices. Credential hashing lives with the table it protects.
- **Batching and backup.** Heartbeats arrive every 30 seconds but persist in minute-sized batches — presence data must survive power cuts without wearing the storage. Backups are atomic whole-file copies taken before any update touches code or data, and they refuse to overwrite an existing backup.
- **Failure translation.** Corrupt or impossible stored values surface as one typed configuration error at the load boundary, so callers handle "the database says something impossible" uniformly instead of pattern-matching parse failures.

## How the System Can Be Extended

Every extension follows the same pattern: define (or reuse) a port, add an adapter or consumer behind it, and keep the contract additive. Concretely:

- **New schedule source or method.** Implement the fetcher or calculator port and register it in the composition root; the fallback chain, staleness flags, and golden-test style pinning apply unchanged. A new calculation method needs recorded reference tables first — fitted parameters ship with tolerance-pinned tests, never bare constants.
- **New device capability (CEC, audio, media store).** Add a port beside the existing ones, implement it in a new adapter module, and consume it from the engine or API without touching the domain. Capabilities never import each other.
- **New display or admin surface.** Build against fixtures and the mock server; consume resolved DTOs only. New needs become fixture requests, not backend imports. Run the theme linter and the contract parity tests before proposing any contract addition — additions are additive fields, never renames, until a versioned revision is justified.
- **New theme.** Scaffold from the theme generator so the sandbox manifest, allowlist, and entry shape stay uniform; themes stay inside the iframe sandbox fed by read-only data.
- **New setting or rule.** Extend the value object with its invariant guard, persist it through the settings boundary, expose it through the same settings API the wizard uses, and fan out a `config-update` so live clients reload. No parallel configuration path.

The composition root (app construction plus the production factory) is the only place that knows which adapters exist; the domain and engine never name one. That single fact is what keeps every item above a bounded change.

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
