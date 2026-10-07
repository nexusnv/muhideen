# Muhideen Architecture

Inspired by paxman-python layering, adapted to a time-driven kiosk system. Single repo, single deployable, logical split enforced by tooling.

This document is the contributor's map: what the system looks like whole, why it looks that way, how the parts relate, and where new work plugs in. Requirements live in `PRD.md`, vocabulary in `CONTEXT.md`, the wire shapes in `docs/api-contract.md` + `api/fixtures/`, lasting decisions in `docs/adr/`, and test organization in `TESTING_STRATEGY.md`.

## System Overview (As Built)

One device serves the jama'ah from one backend. The backend resolves prayer state, and the frontend renders it — the public display (per-display language/theme/dim/carousel overrides from the `displays` map) and the themeable display skin — against the frozen contract. There is no admin UI, no login, and no database: configuration is hand-edited JSON that the backend hot-reloads.

```
External world                    The device (one deployable)
================                  ==========================================

JAKIM e-solat tables ──sync──▶
                               ┌─ BACKEND (landed) ──────────────────────┐
NTP / chrony ──health──▶       │ resolve prayer state + serve it as JSON │
                               │ engine · adapters · api · file config   │
Admin laptop ──SSH──▶        └─────────────────┬───────────────────────┘
    (edit config/*.json,                        │ the contract is the
     drop files in media/)                                  │ only coupling
                                                     ▼
                               ┌─ FRONTEND (landed) ─────────────────────┐
Jama'ah ──reads──▶             │ render resolved state, never compute it │
                               │ display page · themes     │
Chromium kiosk ──pull──▶       └─────────────────────────────────────────┘
```

Backend and frontend are separated by exactly one seam: the versioned JSON contract (`docs/api-contract.md`, executable as Pydantic DTOs, exemplified by `api/fixtures/`). The backend resolves *what* the state is (state, countdown targets, freshness); the frontend renders *how* it looks. The display never computes prayer times, never picks the next prayer, never guesses dim windows. Themes receive read-only data through a narrow seam and can never reach configuration functions.

Still genuinely future, all sitting above or beside that seam without moving it: community theme upload/preview (issue #39), OTA auto-rollback (issue #42), CEC power control (PRD Phase 2), and pre-parse upload body limits (issue #53). Deliberately out of scope and recorded elsewhere: one-time setup tokens (rejected in ADR-0004), per-display schedule/iqamah forks and non-image playlist items (locked in by issue #45). The file-config refactor removed the admin console, credentials/presence storage, and backup/restore UI in favor of hand-edited JSON plus the machine-written timetable cache — none of the remaining surfaces require contract breakage; the contract grows by additive fields.

## Why This Architecture

Each structural choice answers a constraint of the problem: an offline-first kiosk, maintained by volunteers, running on a Debian machine with a kiosk browser.

**Ports-and-adapters (hexagonal) core.** Every external capability — schedule fetching, calculation, storage, time, event fan-out — is consumed through an abstract port defined in `core/`, with the real implementation injected from `adapters/`. This buys two things: testability (the engine is exercised against in-memory fakes with pinned time, no DB or network) and replaceability (a calculation library, an HTTP client, or the whole backend language can be swapped without touching prayer logic). The prayer math never knows what fetched it or what renders it.

**Single repo, single deployable.** The Debian machine installs one artifact and updates one version. A split frontend/backend repo would buy independent versioning at the cost of version skew on a device with no operator — the wrong trade for an appliance. Parallel work still happens: the contract plus fixtures decouple the contributor tracks instead of repository boundaries.

**Contract-first, backend-first.** The backend lands DTOs, fixtures, and OpenAPI before any UI exists, and CI fails on drift between them. Frontend contributors then build against `api/fixtures/` served by a dependency-free mock, with no database, no JAKIM access, and no hardware. Either side is replaceable behind the frozen contract — including the long-term option of a compiled appliance binary.

**Synchronous server, single worker, file config.** There is no database: one JSON file holds the installation's settings, a second JSON file holds the machine-written timetable cache, and media lives as plain files on disk. The server runs sync handlers on one worker; a polling watcher reloads the config live, so there is no restart dance and no concurrent-writer class of bugs on a device nobody babysits.

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

Background work follows the same layering. A one-second ticker recomputes the current event and publishes `state` when anything the client renders changed, plus a per-minute `tick` (failures handled at stage resolution surface as an `error` stage; failures in `Engine.tick`/`next_event` terminate the stream and the client falls back to polling and the route slate); a daily scheduler refreshes the stored year table with classified retries (unrecoverable 4xx rejections never retry; transient failures chain 5m/15m/1h then a re-arming 6h long-pole). Both are composed at startup (the app lifespan) and both drive the same engine use-cases the request path uses — there is no second code path for background computation.

### Backend to frontend: one narrow seam

The frontend relates to the backend exclusively as a consumer of resolved data:

- **Poll the state, stream the changes.** Clients render from `next-event` and stay fresh via the SSE event stream (`state` on transitions, `tick` each minute, `config-update` when settings change), with a 60-second poll as fallback. The client ticks its visible countdown locally between server updates and resyncs on every event.
- **Identify, don't compute.** Each display is a dumb client rendering `/display?id=<id>`, which selects its language/theme/dim/carousel overrides from the `displays` map (unknown ids fall back to the global theme in English). The client never derives anything; there is no registration or heartbeat.
- **Configuration is a file edit.** The admin edits `config/muhideen.json` over SSH — full-replace by hand with live reload fanning out as `config-update` over SSE. There is no login and no parallel configuration path to drift.
- **Fixtures are the frontend's backend.** `api/fixtures/` plus the mock server are the build target for any new surface. If it is not in fixtures and the contract document, the frontend cannot rely on it.

### Internal to external: dependencies and users

External dependencies are kept at arm's length behind ports, and each has a stated degradation story:

| External | Relationship | When it fails |
|---|---|---|
| JAKIM e-solat (unofficial tables) | One polite daily fetch per zone; parsed defensively, never trusted | Keep cache; fall through the offline chain; config can pin calc-only mode |
| NTP / chrony | Read-only health probe with caching; never sets the clock | `time_synced: false` banner; countdowns continue on the monotonic clock |
| System clock + monotonic clock | Sole time sources, injected everywhere | Pinned fakes in tests; drift latch if wall and monotonic disagree |
| Hand-edited JSON + timetable buffer | `muhideen.json` validated at load; buffer rewritten atomically by the sync worker | Invalid edits keep serving last-good and log; corrupt values surface as one typed config error |
| Calculation library | Pure math behind the calc port, golden-tested | Tolerance-pinned outputs; swap caught by golden tests |
| Chromium kiosk | Pull-only display client | Poll fallback covers stream loss; no server-side display state |
| Avahi/mDNS, systemd | Discovery and supervision | Direct-IP fallback; service restarts on failure |

And the three users from the domain glossary each touch a different surface: the **jama'ah** reads only the public display; the **admin** edits `config/*.json` over SSH (and drops media files) and never touches a login; the **integrator** installs hardware and extends the system through the seams described below.

## How Prayer Times Are Derived

Prayer times reach the screen through three stages: acquire candidate schedules, resolve one day, compute the display state. Exactly one zone is configured per installation, and every stage respects that.

**Acquire.** The scheduler fetches the whole calendar year for the configured zone once daily (with classified retries on failure), caching each day in `prayer_buffer.json` with its provenance; a manual day pinned in `muhideen.json` outranks cached days and syncs never overwrite it. Independently, the on-device calculator can derive a day's markers from coordinates plus the current calculation settings. Each stored day carries its source, and each fetch either fully validates or leaves the cache untouched. At resolve time a manual pin (completed against its cached row; partial pins fail loud without one) wins over the cached provider row over calc; calc runs only when no pin and no cached row cover the date.

**Resolve (the fallback chain).** For a requested date, the engine takes the first candidate that matches, in fixed priority:

```
1. manual pin for (date)               → completed against the cached row
2. cached day for (date, zone)      → fresh if recent and first-party
3. calculated day for (date, zone)  → always flagged as fallback provenance
4. last-known saved day for zone    → always flagged stale
5. none of the above                → unknown schedule (404 at the API)
```

Wholesale precedence: a present pin wins over a present cached day over calc; provenance follows the winning day. Calc derivation itself is per marker: 6 from the library plus `imsak_offset_min`/`dhuha_offset_min` offsets (0 hides imsak).

Freshness is a separate flag from identity: anything older than 48 hours or produced by a degraded step renders with a banner, never silently. A requested zone that is not the configured zone is unknown even when coordinates exist — the system never serves a schedule stamped for a zone it was not resolved for. An installation with no settings yet reports itself unconfigured rather than guessing.

**Compute (the state machine).** Given the resolved day, neighboring-day context for midnight crossover, the iqamah rules, and settings, pure functions determine the single current state — `NORMAL`, `PRE_ADHAN`, `ADHAN`, `IQAMAH_COUNTDOWN`, or `SALAH_DIM` — plus the countdown targets. Only Prayer Time Markers (the five prayers, with Jumuah replacing Dhuhr on Friday) can enter a non-normal state; Boundary Time Markers (Imsak, Syuruq, Dhuha) contribute at most an opt-in informational pointer that never influences the state. Iqamah per prayer is either minutes-after-adhan or a fixed clock time, and dimming lasts a configured number of minutes after iqamah (longer for Jumuah). The Hijri date shown alongside is a stored calendar value shifted by the configured regional offset, not computed prayer math.

## Configuration + Media

Two JSON files plus a media tree hold all device state — no database, no migrations. The layer's job is validation and atomicity, never interpretation: repositories map file content to value objects, while ordering, freshness, and invariant guards live in the domain and at the load boundary. Timetable sync is provider-pluggable (`ScheduleClient` port): JAKIM e-solat by zone code, or any Aladhan-compatible `/v1` host by coordinates (`schedule.sync_provider`, method 17/JAKIM default).

- **`muhideen.json` (hand-edited).** Installation identity and tuning in one validated document: `masjid` (name, timezone — no zone code), `schedule` (explicit `sync_provider`, optional served-zone label, `jakim.zone` fetch key and `aladhan` host/method blocks, method, coordinates, calc-only pin, Hijri offset, marker offsets, manual-day pins), `timing` (adhan length, dim minutes, countdown lead, iqamah rules), `adhan_audio`, `theme`, the per-display `displays` map (language, theme overlay, dim override, carousel flag), and `playlists`. The installer copies `config/muhideen.example.json` when no config exists and never touches an existing one; the service fails fast when the file is missing and serves last-good when an edit is invalid. Concurrent settings saves from separate processes — or two operators editing at once — resolve last-writer-wins (saves through one repo instance serialize on a lock).
- **`prayer_buffer.json` (machine-written).** The sync worker's timetable cache: fetched year-table days with source and fetch time. Manual-day pins live in `muhideen.json` (not here) and outrank buffer days at resolve time; the buffer itself is rewritten atomically and a missing file is simply an empty cache. Never hand-edit it — the next successful sync overwrites whatever you write.
- **`media/` (plain files).** Adhan audio served to the displays at the configured `adhan_audio.file` (default `/media/adhan.mp3`), plus playlist image files referenced by the config (template carousel rendering is a future slice). Files are dropped in place (via SSH); nothing is uploaded through the API and nothing is hashed or tracked in a table.
- **Watcher reload semantics.** A polling watcher (~1s interval) watches both JSON files; on change it revalidates and swaps the loaded settings/schedule in place, then publishes `config-update` so live displays refresh — no restart, no login. A failed revalidation keeps serving the last-good snapshot and logs the error; the next valid edit applies normally.
- **Failure translation.** Missing files fail fast at boot; corrupt or impossible stored values surface as one typed configuration error at the load boundary, so callers handle "the config says something impossible" uniformly instead of pattern-matching parse failures.

## How the System Can Be Extended

Every extension follows the same pattern: define (or reuse) a port, add an adapter or consumer behind it, and keep the contract additive. Concretely:

- **New schedule source or method.** Implement the fetcher or calculator port and register it in the composition root; the fallback chain, staleness flags, and golden-test style pinning apply unchanged. A new calculation method needs recorded reference tables first — fitted parameters ship with tolerance-pinned tests, never bare constants.
- **New device capability (CEC, audio, media store).** Add a port beside the existing ones, implement it in a new adapter module, and consume it from the engine or API without touching the domain. Capabilities never import each other.
- **New display or admin surface.** Build against fixtures and the mock server; consume resolved DTOs only. New needs become fixture requests, not backend imports. Run the theme linter and the contract parity tests before proposing any contract addition — additions are additive fields, never renames, until a versioned revision is justified.
- **New theme.** Scaffold from the theme generator so the sandbox manifest, allowlist, and entry shape stay uniform; themes stay inside the iframe sandbox fed by read-only data.
- **New setting or rule.** Extend the value object with its invariant guard, persist it through the file-config boundary (`file_models.py` + example + schema test), and document it in the example config. The watcher hot-reloads it and fans out a `config-update` so live clients refresh. No parallel configuration path.

The composition root (app construction plus the production factory) is the only place that knows which adapters exist; the domain and engine never name one. That single fact is what keeps every item above a bounded change.

## Ownership Boundaries

* Backend-owned: `core/`, `domain/`, `engine/`, `adapters/`, `api/`.
* Frontend-owned: `views/`, `static/`, `themes/`, `api/fixtures/`.
* Cross-boundary change = contract change (`docs/api-contract.md` + fixtures + changelog). CI fails otherwise.

## Quality Enforcement

* **Strict pyright** on `src/` — no `type: ignore`.
* **Ruff** 88 cols — no `noqa` in `src/` (scoped per-file-ignores only).
* **Import-linter** layers (single configured contract, CI-enforced): `api → engine → adapters → domain → core`. Convention, not yet contracted: `views` may use `core` types only, nothing imports `views`, capabilities/themes never import each other.
* **Purity scans**: `domain/`, `engine/`, `core/` must not reference `fastapi`, `httpx`, `datetime.now`, `time.time`; nothing in the tree references the old embedded database anymore (file config is stdlib `json`); `views/` and `themes/` must not reference `domain/`; validation of prayer math never reads presentation flags. The textual scans run in CI (source-scan step) and as `! rg` steps in the contributor quality gate (`CONTRIBUTING.md`); the layer graph is enforced separately by import-linter.
* **Coverage** `fail_under=95`, branch mode. Every new state transition ships a pinned-time test.
