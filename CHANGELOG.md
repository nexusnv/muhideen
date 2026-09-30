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
- Device integration (slice 1A-8): package entrypoints `muhideen`
  (uvicorn service main) and `muhideen-seed` (headless configure-or-sync
  seed); NTP health via the `TimeSyncProbe` port + `time_sync` adapter
  (30s-TTL `timedatectl`/`chronyc` probe, fail-closed) surfaced as the
  additive `time_synced` field on `GET /api/next-event` and SSE `state`
  (FR-1.6 `TIME UNSYNCED` banner + >5s wall-vs-monotonic drift latch);
  `install.sh` (RAM preflight with `--force`, apt deps, offline
  vendored-wheel `uv sync --no-dev`, systemd units, hostname, seed, NTP
  enable, health check, `--dry-run`; first-boot `--zone` gate before any
  mutation; a seed year-fetch failure warns + exits 3 so an offline
  first boot still finishes installing), `update.sh` (dirty-tree refusal,
  `VACUUM INTO` backup before tag checkout, health-verified upgrade +
  `--check`, recovery message instead of auto-rollback), `packaging/`
  units incl. the mDNS advertiser (`muhideen-mdns`,
  `_muhideen._tcp` :8000) and shared `packaging/lib.sh`;
  `tools/build_vendor.sh` (locked `uv export` + host and
  aarch64/x86_64 cross wheels); shipped guide `docs/deployment.md`.
- Configurable calc boundary offsets: imsak_offset_min (0–10, default 10; 0 returns imsak==fajr and hides the marker on display) and dhuha_offset_min (15–30, default 28); fixed defaults replace the latitude-fitted dhuha rule (golden tolerance 3→5); settings API/fixtures/contract carry both keys; resolve merges per marker with API winning.
- Additive `hijri_date` on `GET /api/prayer-day` (hijridate Umm al-Qura, `hijri_offset` applied, null outside library range).
- 1B-1 display: server-rendered `GET /display?id=` (classic-green, stable region IDs, trilingual labels, imsak hidden when disabled, error slates) + static CSS/JS (SSE, monotonic tick, 60s poll, 30s heartbeat).
- 1B-2 states: per-state display variants (PRE_ADHAN note, ADHAN overlay, IQAMAH hero countdown, SALAH_DIM blackout with local 3s long-press skip), Jumuah edges, boundary-never-state replay guard. No contract change.
- 1B-3 admin: `/admin/setup` wizard (thin client, no fork), `/admin/settings` tunables incl. offsets, `/admin/login` with 429 surfacing, server-side QR data URI (LAN URL only), BM/EN toggle. No contract change (HTML-only), no migration. QR via segno (pure-Python, BSD).
- 1C-1 display reskin + fonts: vendored OFL woff2 (Outfit 400/700/800, JetBrains Mono 600/800, latin subsets, `@font-face` in `app.css`, offline-first intact); per-card iqamah times on the display builder (Friday Dhuhr uses the Jumuah rule) plus long-form Hijri date (`hijri_long`, raw wire fallback); display rewritten to the approved example (gradient hero card, H/M/S countdown boxes, giant iqamah view, five cards with iqamah rows, brand-block footer) keeping every existing id/data-attr contract — no contract change.
- 1C-2 Main Stage + countdown settings: `Settings.countdown_before_adhan_min` (default 5, guard 0–90) + per-prayer `countdown_before_adhan_overrides` (persisted as `countdown_min_default` / `countdown_min_<prayer>` keys, additive on settings DTO/fixtures/contract) with `domain.countdown.countdown_window` effective-rule helper; pure-domain `domain.stage.resolve_stage` occupancy engine (`Clock` | `Countdown(kind)` | `Playlist(id)` — countdown windows outrank playlists, else most-recently-activated in-window playlist, else Clock; marker-anchored window math) plus `Playlist`/`PlaylistItem` value objects; additive `stage` id string on SSE `tick` (`"clock"`, `"countdown:adhan:<prayer>"`, `"countdown:iqamah:<prayer>"`, `"playlist:<id>"`) with client reload on stage change.
- 1C-3 playlists: migration `0002_playlists` (`playlists` + `playlist_items` tables, FK cascade, down-migration) with `SqlitePlaylistRepo` CRUD (ordering, active toggle); image pipeline `adapters.images.store_image` reusing the Pillow rules (JPG/PNG/WebP allowlist, 5MB/50-item caps, re-encode + EXIF strip).
- 1C-4 admin + theme knobs: playlist editor UI (`/admin/playlists`, schedule/cycling/items, server-side "on Stage now / next at …" occupancy preview) with admin nav regroup (Profile · Time & Date Marker · Display · Playlists · System) and CSS restyle; per-display theme/dim overrides through the display registry (`display_settings` table, allowlist = `theme.*` + dim keys only, consumed by `GET /display?id=`); `ThemeSettings` closed-enum knobs (palette `classic-green`/`midnight`/`sand`, font `outfit`/`system`, countdown `boxes`/`inline`, clock `24h`/`24h-seconds`/`12h`, hijri `long`/`short`, boundary strip `show`/`hide`, density `comfortable`/`compact` — invalid → 422) persisted as `theme.*` keys (migration `0003_theme_knobs`) and applied in the display builder/templates.

### Changed

- UI is English-only (BM toggle removed); Arabic prayer names stay invariant; `locales/en.json` seeds file-based translation. Display hero marks tomorrow Fajr.
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
- `GET /api/next-event` and SSE `state` payloads gain a required
  `time_synced` boolean (FR-1.6 NTP health; additive — api stays `v1`,
  existing clients ignore the extra key).
- `POST /api/displays/heartbeat` response gains `registered` (additive, api
  stays `v1`): `false` for pending-approval IDs, which are still accepted
  then dropped at flush; fixture + contract updated.
- `delay_minutes` capped at 0–60 (DTO + domain guard, admin input `max=60`);
  existing values ≤60 unaffected; larger stored values now fail loud
  (`ConfigError`/422) instead of stretching countdowns silently.

### Fixed

- `GET /api/prayer-day` with a `zone` other than the configured zone now
  returns 404 (previously a configured calc result was served stamped with
  the requested zone).
- `PUT /api/settings` now rejects (422) rule sets that duplicate a prayer,
  omit a prayer's rule, or declare a `fixed` rule without `fixed_time`;
  previously these saved (or 500'd on duplicates) and later 503'd
  `/api/next-event` and `/api/events`.
- Non-MABIMS calc methods (`MWL`, `ISNA`, `Egyptian`) fall back to MABIMS
  parameters instead of 404-ing on cache miss (the adapter comment already
  promised the fallback).
- A `fixed` iqamah time at or before its adhan now raises `ConfigError`
  (503 on schedule reads until corrected) instead of silently inverting
  the ADHAN/SALAH_DIM windows.
