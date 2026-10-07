# Changelog

All notable changes to this project are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- File-config refactor (no database): the admin UI, auth, SQLite repos,
  migrations, seed, and backup/logs/QR routes are deleted. One hand-edited
  `config/muhideen.json` (validated by `file_models.py`, hot-reloaded in
  ~1s with last-good fallback) plus the machine-written
  `prayer_buffer.json` cache and a plain `media/` tree replace them. The
  kept surface is read-only: `prayer-day`, `next-event`, `events` (SSE),
  `version`, and `/display`. Operator media is served at `/media/*`;
  `/docs` stays public by decision (read-only schemas only). Upgrade
  notes: no DB→JSON importer ships — database installs must recreate
  settings by hand (see `docs/deployment.md`); `--zone`/`--db` flags are
  gone; `update.sh` snapshots only the JSON config.
- Schedule providers: `JAKIMClient` is now the `ScheduleClient` port
  (`fetch_year(settings)`) with two implementations — JAKIM e-solat and
  a new Aladhan-compatible client (one adapter, nested `schedule.aladhan`
  `{base_url, method}` block, so `api.aladhan.com`,
  `aladhan.api.islamic.network`, or any mirror works; `method` defaults to
  17/JAKIM, school
  follows `asr_juristic`, Dhuha derives from Sunrise + `dhuha_offset_min`).
  Switch via `schedule.sync_provider` (required — `"jakim"`, `"aladhan"`,
  or `"none"` for explicit offline; needs coordinates except manual-only;
  changing provider/host/method needs a restart). Synced Aladhan rows are fresh
  provenance (`ALADHAN` banner, no `STALE`).
- Zone codes leave the profile: `masjid` holds only `name` + `timezone`;
  `schedule.jakim.zone` carries codes like `SWK08` (required for
  `"jakim"`), and `schedule.zone` is an optional served-zone label
  (falls back to the fetch key, else `"local"`); synced rows are stamped
  with the label so the engine/buffer zone check keeps hitting.
- Per-marker precedence manual → provider → calc: pins may be partial
  (date + any subset of markers, completed against the stored row; loud
  when uncompletable), the engine prefers pin over cache over calc, and
  calculation (`method` default `MABIMS`) stays the final fallback when
  no pin and no provider row cover the date and coordinates are set.

- Calc backend swap (issues #35/#36): `al-falak==1.0.0` replaces `adhanpy==1.0.5` (same adhan port lineage, maintained, typed); per-method reference parameters (`MABIMS` fitted custom angles, `MWL`/`ISNA`/`Egyptian` built-ins with `ISNA` mapped to North America); `asr_juristic` setting (`shafi`/`hanafi`, default `shafi`) end-to-end with wizard method parity.

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
- Trilingual prayer labels on display hero/cards/boundary strip (English · Arabic · Malay, e.g. Fajr/Subuh, Zohor, Asar, Isyak, Jumaat, Syuruk): runtime source is the triple label tables; `locales/ms.json` is a pinned copy not yet read at runtime; bounds expand from English-only to trilingual; `pre_note`/banners stay English-only; full-UI Malay out of scope. No contract change.
- Per-installation `timezone` (IANA name, default `Asia/Kuala_Lumpur`, existing installs unaffected): setup wizard + settings UI carry the field; `PUT /api/settings` full-replace additive; change takes effect on service restart; fixtures + contract updated.
- Adhan audio: admin-uploaded MP3-only (10MB cap, canonical `adhan.mp3` served via `/static/uploads`), master enable + volume (0–100, default 70), quiet hours (`HH:MM` start/end, both-or-neither, overnight wrap) + per-prayer mute; display plays the clip in the ADHAN overlay only (enabled + file exists + prayer not muted + outside quiet hours); swapping the audio file needs no restart and takes effect on the next ADHAN render; silent by default; settings API/fixtures/contract carry the new keys.
- Failure visibility: SSE tick surfaces config/schedule failures as an `error` stage (display reloads into its 503/404 slate instead of a healthy Clock), and a tick that cannot resolve at all publishes a bare `tick` before re-raising so an open stream drops (poll + reload into the slate) instead of freezing on a healthy frame; playlist occupancy preview returns an `error` reason instead of silent null; sync splits `SyncError(transient)` with non-429 4xx request rejections marked non-transient (no retry) while transient failures (incl. 429 rate limits, which heal) continue on a 6h long-pole chain after the 5m/15m/1h retries; offline-first-no-coordinates slate outcome documented; `_tomorrow`-never-from-last-known invariant kept deliberately.
- Manual day schedule (issue #43): admin-only `PUT /api/manual-day` pins one day's 8-marker `HH:MM` times (no zone in the body — stamped with `settings.zone` server-side; strictly increasing times else 422; repeat PUT replaces) and `DELETE /api/manual-day?date=` releases the pin; pins outrank automatic sources (manual > JAKIM > calc) with `stale: true` plus the MANUAL display banner, and the daily sync skips pinned rows so JAKIM never overwrites them; December/January year-boundary gap bridged by pinning January dates by hand (procedure in `docs/deployment.md`) with automatic recovery at the first January fetch; `/admin/settings` gains a Manual schedule section (date + 8 times + Save/Clear pin).
- One-click backup export/restore + service log viewing (issue #41): admin-only `POST /api/backup/export` downloads the whole installation as one zip (DB snapshot at the zip root + uploads tree under `media/`, filename `muhideen-backup-<ts>.zip`), `POST /api/backup/restore` replaces it from a base64 zip (staged database migrated before it goes live, media swapped atomically, no restart; corrupt/traversal-unsafe archives are 400, oversize payloads 413), and `GET /api/logs` tails the `muhideen` journal (`lines` 1–1000, `available: false` instead of 500 when there is no journal); the System section of `/admin/settings` wires all three (procedure in `docs/deployment.md`; the archive holds password hashes — handle it as secret).
- Playlist `repeat` cycle mode (issue #45): `cycle_mode` ∈ `indefinite|repeat` (`repeat` requires `max_cycles >= 1` and releases the Stage after N full passes; `indefinite` loops forever with `max_cycles: null`); illegal pairings are 422. Playlist items stay image-only and display override scope stays theme+dim-only as intentional v1.0 scope (contract + PRD).
- Display presentation knobs wired to the stylesheet (palette/font/density palettes render; custom_colors rebinds surface tokens; unified countdown format pinned) (#90).

### Changed

- Theme `clock_format` default flips to `12h` (the display screen itself is design-locked 12h and ignores the knob; it stays settable for future surfaces). **Migration note**: `0004_theme_clock_default` rewrites seeded `theme.clock_format = '24h-seconds'` rows to `'12h'` (explicit `24h` choices untouched; pre-1.0, no production installs — downgrading flips `12h` rows back).
- Debian-only v1.0 retarget (issue #46): Raspberry Pi support deferred to a future version (see ADR-0005); kiosk-mode Chromium launch + power-loss/clock-fault behavior documented; human QA checklist at `docs/qa/v1.0-debian-checklist.md`.

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
- Group `carousel_enabled` now drives the display (issue #45): the display
  route resolves the display's group carousel flag into `show_carousel`
  (default on for unknown ids), gating only the `#carousel-dot` footer
  indicator — the footer still renders NORMAL-only, so the FR-3.3 pause
  rule holds.

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
