# Testing Strategy

Tests mirror `ARCHITECTURE.md` layers. Organized by scope, run with `uv`. No skipped tests without justification.

## Test Layers

| Directory | Marker | What it covers |
|-----------|--------|----------------|
| `tests/unit/` | `unit` | Pure domain: state machine, fallback chain, iqamah resolution, Hijri offset. Pinned `FakeClock`, in-memory fakes. No DB, no HTTP, no network. |
| `tests/contract/` | `contract` | API schema vs fixtures: Pydantic DTOs serialize to `api/fixtures/*.json`, SSE sample parses, OpenAPI matches handlers. Fails on drift. |
| `tests/integration/` | `integration` | Engine + real SQLite (tmp file, WAL) + fake JAKIM/calc: seeding, fallback ordering, heartbeat batching, migration `user_version`. |
| `tests/property/` | `property` | Hypothesis invariants: monotonic countdown targets, no overlapping states, Syuruq never dims, midnight crossover always resolves next-day Fajr, re-render idempotence. |
| `tests/e2e/` | `e2e` | Public surface via ASGI test client: `/display`, `/admin` (auth), `/api/next-event`, SSE stream head. No Chromium. |

## Shared Infrastructure

* **Pinned time.** All domain/engine tests inject `FakeClock`; production wires `SystemClock` (monotonic for countdowns). Wall-clock reads outside `adapters/system_clock.py` fail the purity scan.
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

**Negative cases first-class.** Stale banners, calc fallback, parse rejection of bad JAKIM payloads, invalid contracts, and `TIME UNSYNCED` paths are asserted, not just happy paths.

**Provenance of schedules.** Every resolved time carries `source` (`jakim`/`calc`/`manual`) + `fetched_at`; tests assert the flag, not just the clock value.
