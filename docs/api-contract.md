# API Contract (normative)

> Admin surface note: the served surface is the public display reads
> (`prayer-day`, `next-event`, `events` (SSE), `version`, `/display`,
> plus the public config/playlist/display/manual-days/media reads below)
> and the token-gated admin writes (`PATCH`/`POST`/`PUT`/`DELETE` on the
> admin paths, plus `GET /api/backup/export` and `GET /api/logs`).
> Every admin/database section of the pre-file-config API that is not
> reintroduced here (session auth, full-replace settings, legacy
> manual-day/adhan-audio JSON shapes, display groups) answers 404 (see
> "Removed surface"). Fixtures win on conflict, and CI enforces parity
> for the kept surface.

Single-repo logical split. Backend implements first; frontend builds against `api/fixtures/`. Any example here duplicated in fixtures — fixtures win on conflict, and CI enforces parity.

Base URL (device): `http://muhideen.local:8000`. Mock: `http://localhost:8001` via `uv run tools/mock_api.py` (fixed token `dev-admin-token`; in-memory state; canned 422s only — not full validation parity).

## `GET /api/prayer-day?date=YYYY-MM-DD&zone=SGR01`

Day schedule + freshness. `source` is per-day fallback provenance; `stale` drives the banner. `prayers` holds the 5 Prayer Time Markers (cards/hero level); `boundaries` holds Imsak/Syuruq/Dhuha — rendered at secondary level, never adhan/iqamah/dim. `hijri_date` is the Gregorian date converted via `hijridate` with `hijri_offset` applied (`null` outside 1343–1500 AH, never a 500).

```json
{
  "date": "2025-10-20",
  "zone": "SGR01",
  "prayers": {"fajr": "05:45", "dhuhr": "12:15", "asr": "15:30", "maghrib": "18:05", "isha": "19:25"},
  "boundaries": {"imsak": "05:35", "syuruq": "06:55", "dhuha": "07:25"},
  "source": "jakim",
  "stale": false,
  "hijri_date": "1447-04-28"
}
```

## `GET /api/next-event?now=ISO8601`

Server-computed state per PRD §8. Client never computes. All timestamps ISO8601 local.

```json
{
  "state": "IQAMAH_COUNTDOWN",
  "now": "2025-10-20T12:20:00+08:00",
  "next_prayer": "dhuhr",
  "adhan_at": "2025-10-20T12:15:00+08:00",
  "iqamah_at": "2025-10-20T12:30:00+08:00",
  "dim_until": "2025-10-20T12:50:00+08:00",
  "stale": false,
  "next_boundary": "imsak",
  "boundary_at": "2025-10-21T05:35:00+08:00",
  "time_synced": true
}
```

`state` ∈ `NORMAL|PRE_ADHAN|ADHAN|IQAMAH_COUNTDOWN|SALAH_DIM`. `next_prayer` ∈ `fajr|dhuhr|asr|maghrib|isha|jumuah` only (Prayer Time Markers; never a Boundary Time Marker). `next_boundary`/`boundary_at` name the next Boundary Time Marker instant. In `next-event` payloads both keys are required and may be `null`; they are non-null only when the installation opts in (`boundary_countdown` setting, default off). In SSE `state` payloads `None` values are omitted, so the keys appear only when the opt-in is on. They never influence `state`. `time_synced` (FR-1.6) is always required and non-null: `false` when the system clock is unsynchronised or a wall-clock step was detected (the `TIME UNSYNCED` banner), `true` otherwise.

## `GET /api/events` (SSE `text/event-stream`)

Events: `state` (on transition), `tick` (1/min heartbeat with server `now` + Main Stage id `stage`; the display reloads when `stage` changes), `config-update` (settings/theme/playlist changed → refetch). `state` payloads always carry `time_synced` (FR-1.6) and carry `next_boundary`/`boundary_at` when the opt-in is on (see sample). `stage` is one of `clock`, `countdown:adhan:<prayer>`, `countdown:iqamah:<prayer>`, `playlist:<id>`, or `error` (config/schedule failure — the display reloads into the route slate instead of rendering a healthy Clock). `60s` poll of `next-event` is the fallback. Sample in `api/fixtures/events-stream.txt`. OpenAPI documents the three payload schemas inline as an `anyOf` (under `type: object`) beneath the `text/event-stream` content.

## `GET /api/version`

```json
{"version": "0.1.0", "api": "v1"}
```

## Admin auth (static Bearer token)

No sessions, no users, no login rate-limit. The device holds one 256-bit token in `<config-dir>/admin_token` (`0600`, service-owned, generated once by `install.sh`, never clobbered; rotation = rewrite the file + restart). The service reads it once at boot (`--admin-token-file`, default `<config-dir>/admin_token`); a missing/blank/unreadable file disables every gated endpoint (`503 {"detail": "admin writes disabled"}`, even with a Bearer header) while public reads keep serving. Every other gated request carries `Authorization: Bearer <token>`; missing/mismatched/empty answers `401 {"detail": "invalid admin token"}` with `WWW-Authenticate: Bearer`. Every gated request (writes + gated GETs) logs one INFO audit line `peer method path -> status` (path only — never query, headers, or tokens). Plaintext HTTP on the LAN means the Bearer is sniffable locally: run on a trusted LAN / isolated IoT VLAN.

Gated: every `PATCH`/`POST`/`PUT`/`DELETE` on `/api/config/*`, `/api/playlists/*`, `/api/displays/*`, `/api/media/*`, `/api/backup/restore`, every `.../validate` dry-run POST, plus `GET /api/backup/export` (bundles buffer+media) and `GET /api/logs` (leaks journald paths/errors). Reads stay public: `prayer-day`, `next-event`, `events`, `version`, `display`, `GET /api/config` + section GETs, `GET /api/playlists` + `preview` + detail, `GET /api/displays`, `GET /api/config/manual-days`, `GET /api/media`.

## `GET /api/config` + `GET /api/config/{masjid,schedule,timing,theme,adhan-audio}`

Public. Full file (export source, reserialized with `$schemaVersion`) and one parsed section; unknown section names are 404.

## `PATCH /api/config/masjid`

Gated. All-optional partial (`{"name", "timezone"}`); omitted = unchanged, explicit `null` → 422. Free text is stripped, whitespace-only → 422. Unknown timezone → 422. Response `{ok, restart_required}` (`true` only when `timezone` changed — restart to apply). Fixtures: `api/fixtures/admin-masjid-200.json` (request), `api/fixtures/admin-masjid-422.json` (blank-name 422 sample).

```json
{"name": "Masjid An-Nur", "timezone": "Asia/Kuala_Lumpur"}
```

```json
{"ok": true, "restart_required": false}
```

## `PATCH /api/config/schedule`

Gated. All-optional partial (`sync_provider`, `zone`, nested `jakim`/`aladhan` (one-level deep merge — sibling keys survive), `method` (`MABIMS`/`MWL`/`ISNA`/`Egyptian`), `asr_juristic`, `lat`/`lon`, `calc_only`, `hijri_offset` `-2..2`, `imsak_offset_min` `0..10`, `dhuha_offset_min` `15..30`, `boundary_countdown`, `manual_days_file`). `lat`+`lon` paired-or-null; `jakim.zone` required iff provider is `jakim`; `lat`/`lon` required iff provider is `aladhan`; inline `manual_days` + `manual_days_file` ref stay exclusive (422). `restart_required=true` only for the effective sync client (`sync_provider` flip, or `aladhan.*` while provider is `aladhan`). Fixtures: `api/fixtures/admin-schedule-200.json`, `api/fixtures/admin-schedule-422.json` (provider switch without coordinates).

```json
{"boundary_countdown": true, "jakim": {"zone": "SGR01"}}
```

```json
{"ok": true, "restart_required": false}
```

## `PATCH /api/config/timing`

Gated. `{adhan_duration_s > 0, dim_minutes_default/dim_minutes_jumuah 5..60, countdown_before_adhan_min 0..90, countdown_before_adhan_overrides {prayer: 0..90}, iqamah_rules[6]}`. Override keys are prayer-only (`fajr,dhuhr,asr,maghrib,isha,jumuah`; boundary/unknown → 422; `dhuhr` ≠ `jumuah`). `iqamah_rules` is all-6 full replace (exactly once each; `delay 0..60` / `fixed` requires `fixed_time`). Never needs a restart.

## `PATCH /api/config/theme`

Gated. Seven closed enums, all optional, all non-nullable (`null` → 422): `palette` ∈ `classic-green|midnight|sand`, `font` ∈ `outfit|system`, `countdown_style` ∈ `boxes|inline`, `clock_format` ∈ `24h|24h-seconds|12h`, `hijri_form` ∈ `long|short`, `boundary_strip` ∈ `show|hide`, `density` ∈ `comfortable|compact`. Never needs a restart.

## `PATCH /api/config/adhan-audio`

Gated. `{enabled, volume 0..100, quiet_hours_start/end paired HH:MM|null, muted_prayers[] prayer-only, file (path-string only)}`. Volume/quiet-hours/mutes validate even when `enabled=false`; only `file` is ignored while disabled. Binaries arrive via `POST /api/media` (`kind=adhan`).

## `POST /api/config/validate` + `POST /api/config/{masjid,schedule,timing,theme,adhan-audio}/validate`

Gated dry-runs (Bearer required, nothing written, same `422 [{loc,msg}]` shape). Full-file validate takes the whole `ConfigFile` (`$schemaVersion` alias spelling); section validates merge the partial over the live file with the PATCH depth rule. `POST /api/config/manual-days/validate` takes a bare pins array (structural + buffer-completion, same pre-sync trap as PUT).

## Playlists + items + preview

`GET /api/playlists`, `GET /api/playlists/{id}`, `GET /api/playlists/preview?moment=<tz-aware ISO>` are public. The rest is gated. Ids are `1-64 [A-Za-z0-9_-]` on writes (legacy rows list as-is; slash-ids stay routing-404). `POST /api/playlists` requires `{id, title}` (`201`; duplicate `id` → 409). `PATCH /api/playlists/{id}` is a partial merge — `id` rename and any `items` key → 422. Windows are `HH:MM` clock bounds or marker names (or `null`); `anchor_marker` is prayer-only (boundary → 422); `cycle_mode=repeat` requires `max_cycles >= 1`, `indefinite` requires `max_cycles=null` (else 422). Items: `{image_path` (media-relative, dangling refs allowed — upload before or after), `duration_s > 0`, `sort_order >= 0 unique}`; malformed → 422, duplicate `sort_order` on POST → 409, omitted → `max(existing)+1` (empty → 0); ambiguous `sort_order` DELETE → 409 with a hand-fix detail. `preview` resolves one moment's stage plus active ids (`{stage, active_playlists[]}`); missing/naive `moment` → 422. Fixtures: `api/fixtures/admin-playlist-201.json`, `api/fixtures/admin-playlist-422.json`.

```json
{"id": "taraweeh", "title": "Taraweeh Slides"}
```

## Displays

`GET /api/displays` is public (id → entry map). `PUT /api/displays/{id}` (upsert: `201` create / `200` replace; `{}` resets to all-defaults), `PATCH /api/displays/{id}` (partial merge; `theme.<knob>=null` inherits that knob), `DELETE /api/displays/{id}` are gated. Body: `{name (1-200|null), language en|ms|ar|bm, theme (partial-7, each knob enum|null=inherits), dim_minutes_override 5-60|null, carousel_enabled bool}`; blank `name` / unknown `language` / null non-nullables → 422; unknown id on PATCH/DELETE → 404. No schedule/iqamah fork (theme+dim only). Fixtures: `api/fixtures/admin-display-201.json`, `api/fixtures/admin-display-422.json`.

```json
{"carousel_enabled": false, "dim_minutes_override": 30, "language": "ms", "name": "Lobby", "theme": {"palette": "midnight"}}
```

## Manual-days pins

`GET /api/config/manual-days` is public (`{source: "inline"|"file", pins: [...]}`; ref-set-but-absent → 503 hand-fix detail, never empty `pins: []`). `PUT /api/config/manual-days/{date}` (upsert, `200` — never 409; path date authoritative, body mismatch → 422; ≥1 of the 8 `HH:MM` markers) and `DELETE` same (`404` unknown; ref-set-but-absent → 503) are gated. Writes route by the pins ref (file) or inline (ref changes only via schedule PATCH; orphan pins files are never auto-deleted). Validation is two-tier: structural always, plus buffer-completion — a partial pin with no buffer row for its date+zone fails `422` with `"no buffer row for <date> — sync first or send a full-day pin"`. Fixtures: `api/fixtures/admin-pin-200.json`, `api/fixtures/admin-pin-422.json`.

```json
{"asr": "16:30", "date": "2026-05-01", "dhuha": "07:33", "dhuhr": "13:15", "fajr": "05:58", "imsak": "05:48", "isha": "20:30", "maghrib": "19:15", "syuruq": "07:05"}
```

## Media

`GET /api/media` is public (`[{path, size_bytes}]`, media-relative paths). `POST /api/media` (`multipart/form-data`: `file` binary + `kind` ∈ `adhan|image`) and `DELETE /api/media/{path}` are gated. `adhan` lands at the media root, `image` under `playlists/`; basenames flatten (no subdirs v1); overwrite of a relpath is allowed (`200`, new file `201`, body `{path}`). Basenames reject empty/NUL/`?`/`#`/trailing-`/`/backslash/drive/absolute/`..`/overlong (422). Content: images `jpg|jpeg|png|webp` ≤5MB (Pillow open-verified + re-encoded, EXIF stripped), audio `mp3` ≤10MB (ID3/frame-sync magic); oversize → 413 via bounded streaming reads (never buffered-then-checked); kind/ext mismatch → 422. `DELETE` is unconditional (dangling refs allowed, no reference scan); unknown → 404. Fixture: `api/fixtures/admin-media-201.json`.

```json
{"path": "playlists/slide.png"}
```

## `GET /api/backup/export`

Gated. Downloads the installation as one `application/zip` (`Content-Disposition: attachment; filename="muhideen-backup-<YYYYMMDD-HHMMSS>.zip"`). Members: `muhideen.json` + `pins.json` (when the ref is set) + `prayer_buffer.json` (when present — byte-copied as-is, even corrupt; backup is not validation) + `manifest.json` (`{files: [{path, size_bytes, sha256}], exported_at}`, sample in `api/fixtures/admin-backup-manifest.json`) + media binaries under `media/`. Every member path is relative, never absolute. Media sizes sum before archiving: over 50MB uncompressed → `413`; the zip streams (binaries are never all held in memory). Treat the download as secret (it restores operator configuration).

## `POST /api/backup/restore`

Gated. Replaces the installation from a `multipart/form-data` backup zip (`file` field, ≤50MB upload via bounded streaming → `413`). Hardens zip-slip (absolute, `..`, symlink, drive-letter members rejected → 422), then validates everything — config (`ConfigFile` + settings), pins (pins-file semantics), buffer schema, staged pins completion against the STAGED buffer (not the live one), media relpaths + total-size cap — BEFORE touching live disk (any failure → 422/413, disk untouched, staging kept for forensics). Applies staged-atomic through a same-filesystem staging dir (`<config-dir>/.restore-*.tmp`, removed on success, kept on failure until the next restore): ordered renames config → pins → buffer, pre-rename backups for rollback, additive per-file tmp+rename media (same relpath overwrites, unlisted files are never deleted — orphans persist). Not single-rename atomic: the watcher may emit one transient mixed-generation `config-update` (new config + old pins/buffer) before converging; a scheduler `save_day` racing the buffer rename is accepted last-wins (next sync heals). Success is `200 {"ok": true}` (fixture `api/fixtures/admin-restore-ok.json`); validation failures pin the member (`api/fixtures/admin-backup-422.json`).

```json
{"ok": true}
```

## `GET /api/logs?lines=200`

Gated. Tails the `muhideen` unit journal newest-last as `text/plain` (sample lines in `api/fixtures/admin-logs.txt`). `lines` is an int `1-1000` (default `200`; non-int/out-of-range → 422). Runs `journalctl -u muhideen --no-pager -n <lines>` via argv (no shell) with a 5s timeout; timeout, missing binary, failing exit, and journald-less hosts (compose/dev) fail soft to `501 {"detail": ...}` — never 500.

## Removed surface

Still 404 (session/database era, never returning): `/admin*` pages, `/api/settings`, `/api/auth/*`, `PUT /api/settings`, `PUT`/`DELETE /api/manual-day`, `POST`/`DELETE /api/adhan-audio` (base64 JSON), `PATCH /api/display-groups/*`.

## Errors

Unknown ids/dates/paths/sections are 404 with a detail message. Missing or invalid configuration is 503: the wire detail is path-scrubbed (`config: …`; the full path goes to the server log), while domain causes (e.g. a `fixed` iqamah time at or before its adhan) pass through verbatim. Invalid bodies and query inputs are 422 (`{detail: [{loc, msg, type}]}`, body-relative locs like `["body", "jakim", "zone"]`, query locs like `["query", "lines"]`); naive `now`/`moment` values are 422. Duplicate playlist ids / item orders are 409. Oversize uploads/exports are 413. Missing/wrong admin tokens are 401 (+`WWW-Authenticate: Bearer`); a missing/unreadable token file disables gated endpoints with 503 `admin writes disabled` (distinct from broken-config 503s). Missing journals/units are 501. `/docs`, `/redoc`, and `/openapi.json` are public by decision (read-only schemas only, OpenAPI carries per-endpoint 422 examples). No stack traces or absolute paths on the wire.

## Versioning
Additive fields allowed without bump — e.g. `time_synced` on `next-event` and SSE `state` payloads (slice 1A-8), and `stage` on SSE `tick` payloads. Renames/removals/semantic changes require `/api/v2/...` + fixtures + changelog + migration note.

Pre-consumer amendments: before the first frontend consumer lands (1B-1), semantic corrections may amend v1 fixtures + this document in place with a CHANGELOG migration note instead of standing up `/api/v2`; slice 1A-4a is exercised under this clause.
