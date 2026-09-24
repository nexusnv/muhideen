# Changelog

All notable changes to this project are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Executable contract (slice 1A-3): Pydantic DTOs for `GET /api/prayer-day`,
  `GET /api/next-event`, SSE events, `POST /api/displays/heartbeat`, and
  `GET /api/version`, plus `create_app()` with the five contract routes
  declared (stub handlers return 501 until slice 1A-7).
- Normative fixtures `api/fixtures/heartbeat-request.json`,
  `api/fixtures/heartbeat-response.json`, `api/fixtures/version.json`.
- Contract tests enforcing DTO ↔ fixtures ↔ `docs/api-contract.md` ↔ OpenAPI
  parity — any drift fails CI.
- This changelog: contract changes now ship fixtures + doc + changelog entry
  together (`CONTRIBUTING.md`).
- Engine orchestration (slice 1A-4): `muhideen.engine.Engine` resolves day
  schedules through the FR-1.2 fallback chain (cache → calc → last-known)
  over the `PrayerRepo`/`SettingsRepo`/`CalcEngine`/`Clock` ports, computes
  the PRD §8 next event, and fans out `state`/`tick` on the `EventBus`.
- Integration tests for the engine on in-memory fakes: fallback ordering and
  lazy port queries, zone stamping of calc days, settings hot-reload,
  midnight crossover, stale provenance, and `["state", "tick"]` fan-out
  order.
- Property tests (Hypothesis) for the state machine: monotonic targets,
  non-overlapping state windows, Syuruq never dims, midnight always resolves
  next-day Fajr, and re-render idempotence.
- Marker taxonomy (slice 1A-4a): `MarkerName`/`MarkerKind`/`marker_kind`
  (PRD FR-1.7); Imsak/Dhuha on `PrayerDay` + `prayers`/`boundaries` contract
  split; opt-in boundary countdown (`Settings.boundary_countdown`,
  `next_boundary`/`boundary_at` on next-event + state payloads).
- SQLite persistence (slice 1A-5): hand-rolled migration runner on
  `PRAGMA user_version` shipping the full PRD §6.2 v0.1 schema (all seven
  tables) with FR-1.4 iqamah-rule, settings-default, and `Default`-group
  seeds, plus idempotent down-migrations; `SqlitePrayerRepo` /
  `SqliteSettingsRepo` behind the existing ports (WAL,
  `synchronous=NORMAL`, single-connection/single-lock single-writer
  discipline, first-boot `ConfigError` until the setup wizard runs,
  `ValueError`→`ConfigError` translation at the load boundary);
  `VACUUM INTO` backup primitive; 60s-batched heartbeat writes
  (`SqliteDisplayRepo`); integration suite on tmp-file SQLite covering
  migration upgrade/downgrade round-trips and engine-over-SQLite wiring.
- Schedule sources (slice 1A-6): defensive JAKIM e-solat client
  (`period=year`, pinned UA/15s timeout, 2s/4s/8s in-client backoff for
  5xx/transport failures — any 4xx incl. 429 fails fast, paced by the
  02:00 chain —, adapter-side naming map, parse + ordering rejection
  incl. empty or incomplete payloads keeping cache intact); `MabimsCalcEngine` MABIMS fallback deriving all 8 markers per
  recorded research (golden-tested against captured JAKIM year tables);
  02:00 scheduler with FR-1.1 5m/15m/1h retry chain (injected `Clock`,
  APScheduler, never started in tests); `domain.ordering.ensure_ordered`;
  captured payloads `tests/data/` plus the recorded source research
  pinning the MABIMS params, the dhuha latitude rule, and the golden
  tolerance.
- HTTP surface (slice 1A-7): real FastAPI `def` sync handlers replacing the
  501 stubs (tz-aware `now` validation with 422 on naive input, `date`/`zone`
  input handling, `ScheduleError`→404 and `ConfigError`→503 mapping), SSE
  stream with `sse_bus` fan-out plus `system_clock` injection, settings API
  with `boundary_countdown`/`calc_only` (live-reload via `config-update`),
  Argon2id single-admin auth with 30-min expiring sessions and 5/min/IP rate
  limits, LAN+admin-gated `/docs`, and lifespan migrate/ticker/scheduler
  wiring with `create_production_app` factory; 4 fixtures
  (`settings.json`, `auth-request.json`, `auth-response.json`,
  `session.json`) + 6 contract sections.

### Changed

- `PrayerName` renamed `MarkerName`; state machine windows are
  Prayer-Time-Marker-only (boundary markers never PRE_ADHAN/ADHAN/IQAMAH/dim;
  `resolve_iqamah` raises for them); `next_prayer` narrowed to prayer markers
  (**migration note**: `GET /api/prayer-day` `times` → `prayers` +
  `boundaries`, `GET /api/next-event` gains `next_boundary`/`boundary_at` —
  contract v1 amended pre-consumer under the new `docs/api-contract.md`
  versioning clause, no `/api/v2`); PRD Rev 3 + docs/skills sweep.
- `Settings` gains `iqamah_rules` (FR-1.4 defaults: Subuh 15, Dhuhr/Asr/
  Maghrib 10, Isha 15, Jumuah own rule) plus `lat`/`lon`/`method` calc
  configuration (PRD §6.2), with construction guards pairing and ranging the
  coordinates.
- `PrayerRepo` gains `last_known(day, zone)` (FR-1.2 step 3); no adapter
  implemented the port yet, so the surface change is free.
- `docs/api-contract.md`: heartbeat response body `{"ok": true}` documented
  (was undocumented; additive, no version bump).
- `tools/mock_api.py`: serves version and heartbeat responses from fixture
  files instead of inline literals, so the mock cannot drift from fixtures.
- `core.ports` gains `DisplayRepo` (`record_seen` buffer + `flush`):
  heartbeats are written in 60s batches, never per-call (PRD §5.2/§6.3);
  display IDs that are not pre-registered are dropped at flush
  (contract: IDs are pre-registered or pending-approval).
- **Migration note:** `core.ports.JAKIMClient.fetch_week` renamed
  `fetch_year` (one `period=year` call covers FR-1.1's window; zero
  consumers outside this slice).
- `httpx>=0.27` promoted from the dev group to runtime dependencies (the
  client needs it at runtime; already in the lockfile).
- Added `adhanpy==1.0.5` (MIT, zero runtime deps) as the MABIMS calc
  engine library.
- `EventBus.publish` gains an optional `changed` tuple (config-update groups;
  default empty, old publishers unaffected); `create_app` now takes required
  `AppDeps` (no module-level app).
- Added `argon2-cffi>=23.1` (Argon2id password hashing for the admin user).

### Fixed

- `GET /api/prayer-day` with a `zone` other than the configured zone now
  returns 404 (previously a configured calc result was served stamped with
  the requested zone).
- `PUT /api/settings` now rejects (422) rule sets that duplicate a prayer,
  omit a prayer's rule, or declare a `fixed` rule without `fixed_time`;
  previously these saved (or 500'd on duplicates) and later 503'd
  `/api/next-event` and `/api/events`.
