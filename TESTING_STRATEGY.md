# Testing Strategy

Tests mirror `ARCHITECTURE.md` layers. Organized by scope, run with `uv`. No skipped tests without justification.

## Test Layers

| Directory | Marker | What it covers |
|-----------|--------|----------------|
| `tests/unit/` | `unit` | Pure domain: state machine, fallback chain, iqamah resolution, Hijri offset. Pinned `FakeClock`, in-memory fakes. No DB, no HTTP, no network. |
| `tests/contract/` | `contract` | API schema vs fixtures: Pydantic DTOs serialize to `api/fixtures/*.json`, SSE sample parses, OpenAPI matches handlers. Fails on drift. |
| `tests/integration/` | `integration` | Engine + real SQLite (tmp file, WAL) + fake JAKIM/calc: seeding, fallback ordering, heartbeat batching, migration `user_version`. |
| `tests/property/` | `property` | Hypothesis invariants: monotonic countdown targets, no overlapping states, Boundary Time Markers never adhan/iqamah/dim/leave NORMAL, opt-in pointer gates exactly, midnight crossover always resolves next-day Fajr, re-render idempotence. |
| `tests/e2e/` | `e2e` | Public surface via ASGI test client: landed slice 1A-7 (API surface, SSE, auth/rate limits, docs gate); `/display` and `/admin` join with their frontend slices. No Chromium. |

## Shared Infrastructure

* **Pinned time.** All domain/engine tests inject `FakeClock`; production wires the `SystemClock` adapter (`adapters/system_clock.py`, via `create_production_app`) for wall time and monotonic countdowns. Wall-clock reads in `domain/`, `engine/`, `core/` fail the purity scan (CI source-scan step, plus the `! rg` steps in the `CONTRIBUTING.md` quality gate).
* **Test doubles stay local.** Each file defines its own fakes (`FakePrayerRepo`, `StubJAKIM`, `FakeClock`). No shared mock library.
* **Isolation.** SQLite tests use tmp files; contract tests never touch the DB; e2e resets between tests.
* **Hypothesis profile.** `tests/conftest.py` registers `ci` (`max_examples=100`, no deadline) matching paxman practice.

## Running

```bash
uv sync --all-extras
uv run pytest
uv run pytest -m unit
uv run pytest -m contract
uv run pytest -m integration
uv run pytest -m property
uv run pytest -m e2e
uv run pytest --cov=muhideen --cov-report=term-missing
```

## Design Principles

**Tests mirror architecture.** Unit covers `domain/`, contract covers `api/` DTOs, integration covers `engine/` + `adapters/`, property locks cross-cutting time invariants, e2e covers the served surface.

**Time is testable.** The pinned-clock test (same `now` + snapshot + settings → identical next-event) is the most important test, equivalent to paxman's determinism test.

**Negative cases first-class.** Stale banners, calc fallback, invalid contracts, and first-boot `ConfigError` are asserted today, not just happy paths. JAKIM payload-parse rejection, ordering validation, and sync retry/backoff are asserted since slice 1A-6; `TIME UNSYNCED` joins when device integration lands.

**Provenance of schedules.** Every resolved time carries `source` (`jakim`/`calc`/`manual`) + `fetched_at`; tests assert the flag, not just the clock value.
