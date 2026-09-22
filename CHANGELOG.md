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

### Changed

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
