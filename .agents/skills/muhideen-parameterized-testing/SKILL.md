---
name: muhideen-parameterized-testing
description: Run a black-box parameterized sweep for Muhideen schedule resolution: generate ~100 mixed pinned-time scenarios with per-row expected state/source/stale, execute them through the HTTP API, and report divergences as findings. Use after a backend slice lands (acceptance), before releases touching domain/engine, or to pin fallback and stale-flag splits. No source modifications.
---

# Muhideen Parameterized Testing

Black-box acceptance through the **HTTP API only** — act as a display client, not a developer. Expectations are recorded at generation time from the spec and fixtures, never snapshotted from observed output.

## When to Use

- After a 1A slice lands — acceptance before the frontend builds on it.
- After domain/engine changes — unit suites prove no regression; this proves the *experience* (states, countdown targets, stale banners) still reads sanely across times and zones.
- To pin fallback-chain and `stale`-flag semantics beyond unit vectors.

**When NOT to use:** instead of unit/property tests (complements them); for kiosk rendering checks (browser territory); when fewer than ~20 meaningful scenarios exist (hand table is cheaper).

## Protocol

### 1. Generate scenarios (~100 rows, seeded RNG)
Matrix over pinned `now` × zone/method × schedule condition:
- ~55 nominal: each state (`NORMAL/PRE_ADHAN/ADHAN/IQAMAH_COUNTDOWN/SALAH_DIM`) × prayers, Jumuah Friday, midnight crossover, exact-boundary `now` values; Boundary Time Markers (imsak/syuruq/dhuha) pinned to stay NORMAL with pointer on/off; `next_prayer` never a boundary value.
- ~45 degraded: empty cache, expired cache (>48h), malformed feed payload, calc-only mode, unknown zone, DST transition day, leap day, unsynced-clock flag.
- Per row: `{"now", "zone_or_method", "schedule_state", "expect_state", "expect_source", "expect_stale", "mode": "assert"|"explore", "why"}` as JSON. Seed recorded. Explore rows ≤20%.

### 2. Choose request variants (relevant only)
- Always: `GET /api/prayer-day`, `GET /api/next-event?now=`, SSE stream head.
- Never: combinatorial auth/rate-limit matrices (unit territory); combinatorial theme variants (frontend territory).

### 3. Run (HTTP only)
- Against `uv run tools/mock_api.py` for fixture-shape checks, or a test-server with seeded DB for resolution checks — never both muddled in one table.
- Disposable scripts live in `/tmp/opencode/`, never in the repo. Read-only except the report file.

### 4. Analyze
- Distribution per variant (states, sources, stale counts).
- Every `assert` row vs expectation; triage mismatches into (a) implementation bug, (b) spec/fixture correction with evidence, (c) follow-up issue.
- Run the matrix twice; require byte-identical results (determinism scoped to clock + snapshot).
- Report `explore` rows as observations, grouped by theme.

### 5. Report + clean up
- Save to `docs/development/reports/YYYY-MM-DD-<scope>-sweep.md`: method (seed, counts, variants), distribution table, numbered findings with vectors + verdicts, follow-up issues raised.
- Delete disposable scripts and data. Repo diff shows only the report.

## Pass Bar

- 100% of `assert` rows match (or each mismatch dispositioned with evidence).
- Zero unexpected exception types / 5xx.
- Zero wrong-state rows (wrong countdown target fails even if counts look healthy — spot-check values, not just states).
- Every follow-up has an issue number or a written reason for none.

## Anti-Patterns

- Generating expectations from the implementation instead of the spec/fixtures.
- Snapshotting observed output as expected post-hoc.
- Touching `src/`, `tests/`, or configs mid-sweep.
- Leaving scripts/data in the repo.
