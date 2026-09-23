# ADR-0003: Raw sqlite3 Behind Repository Ports

Status: Accepted
Date: 2026-09-22

## Context
Need zero-admin single-file persistence on Pi with testable, swappable storage. Options: stdlib `sqlite3` + hand-rolled migrations, or SQLAlchemy 2.0 now.

## Decision
Stdlib `sqlite3` (WAL, `synchronous=NORMAL`) behind the repository ports (`PrayerRepo`, `SettingsRepo`, `DisplayRepo`). Hand-rolled `migrations/*.sql` with `PRAGMA user_version`. In-memory fake repos for unit/contract tests; tmp-file SQLite for integration. Backup via `VACUUM INTO`.

## Rationale
* Zero extra deps and Pi wheels to manage; stdlib is always present.
* Ports keep ORM adoption reversible — SQLAlchemy later needs no domain/engine changes.
* Single writer thread + 60s heartbeat batching removes the only concurrency risk an ORM would hide.

## Consequences
* Migration discipline is manual — every schema change ships an idempotent `*.sql` + version bump + test.
* Revisit ORM only on second-writer need or query complexity, not before.
