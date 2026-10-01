# API Contract (normative)

Single-repo logical split. Backend implements first; frontend builds against `api/fixtures/`. Any example here duplicated in fixtures — fixtures win on conflict, and CI enforces parity.

Base URL (device): `http://muhideen.local:8000`. Mock: `http://localhost:8001` via `uv run tools/mock_api.py`.

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

Events: `state` (on transition), `tick` (1/min heartbeat with server `now` + Main Stage id `stage`; the display reloads when `stage` changes), `config-update` (settings/theme/playlist changed → refetch). `state` payloads always carry `time_synced` (FR-1.6) and carry `next_boundary`/`boundary_at` when the opt-in is on (see sample). `stage` is one of `clock`, `countdown:adhan:<prayer>`, `countdown:iqamah:<prayer>`, or `playlist:<id>`. `60s` poll of `next-event` is the fallback. Sample in `api/fixtures/events-stream.txt`. OpenAPI documents the three payload schemas inline as an `anyOf` (under `type: object`) beneath the `text/event-stream` content.

## `POST /api/displays/heartbeat`

Request body:

```json
{"id": "HALL-01"}
```

Response body (`200 OK`):

```json
{"ok": true, "registered": true}
```

Server records `last_seen`/IP/group server-side in 60s batches. No auth; LAN-only; IDs pre-registered or pending-approval. `registered` is `false` for pending-approval IDs — the heartbeat is still accepted, then matches no row at flush and is dropped.

## `GET /api/version`

```json
{"version": "0.1.0", "api": "v1"}
```

## `GET /api/settings`

Admin session required. Full installation settings including the boundary offsets (imsak 0–10 default 10 with 0 hiding imsak on display; dhuha 15–30 default 28), the calculation `method` (`MABIMS`/`MWL`/`ISNA`/`Egyptian`) plus `asr_juristic` (`shafi`/`hanafi`, default `shafi`), the `boundary_countdown` opt-in and `calc_only` offline mode, the pre-adhan Stage-takeover window (global default 5 min, range 0–90, with optional per-prayer overrides keyed by prayer name; absent prayer = default), and the `theme` knobs (closed enums: `palette` ∈ `classic-green|midnight|sand`, `font` ∈ `outfit|system`, `countdown_style` ∈ `boxes|inline`, `clock_format` ∈ `24h|24h-seconds|12h`, `hijri_form` ∈ `long|short`, `boundary_strip` ∈ `show|hide`, `density` ∈ `comfortable|compact`; anything else is 422). Per-display `display_settings` rows (`theme.*` plus `dim_minutes_override` 5–60) override the knobs and dim for one `GET /display?id=` render.

```json
{
  "masjid_name": "Masjid Test",
  "zone": "SGR01",
  "hijri_offset": 0,
  "imsak_offset_min": 10,
  "adhan_duration_s": 180,
  "dim_minutes_default": 20,
  "dim_minutes_jumuah": 45,
  "iqamah_rules": [
    {"prayer": "fajr", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
    {"prayer": "dhuhr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "asr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "maghrib", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "isha", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
    {"prayer": "jumuah", "mode": "delay", "delay_minutes": 10, "fixed_time": null}
  ],
  "lat": 3.07,
  "lon": 101.69,
  "method": "MABIMS",
  "asr_juristic": "shafi",
  "dhuha_offset_min": 28,
  "boundary_countdown": false,
  "calc_only": false,
  "countdown_before_adhan_min": 5,
  "countdown_before_adhan_overrides": {"fajr": 10},
  "theme": {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "24h-seconds",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable"
  }
}
```

## `PUT /api/settings`

Admin session required. Full-replace body; the response echoes the stored settings. A successful write publishes a `config-update` event with the `settings` group.

```json
{
  "masjid_name": "Masjid Test",
  "zone": "SGR01",
  "hijri_offset": 0,
  "imsak_offset_min": 10,
  "adhan_duration_s": 180,
  "dim_minutes_default": 20,
  "dim_minutes_jumuah": 45,
  "iqamah_rules": [
    {"prayer": "fajr", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
    {"prayer": "dhuhr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "asr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "maghrib", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
    {"prayer": "isha", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
    {"prayer": "jumuah", "mode": "delay", "delay_minutes": 10, "fixed_time": null}
  ],
  "lat": 3.07,
  "lon": 101.69,
  "method": "MABIMS",
  "asr_juristic": "shafi",
  "dhuha_offset_min": 28,
  "boundary_countdown": false,
  "calc_only": false,
  "countdown_before_adhan_min": 5,
  "countdown_before_adhan_overrides": {"fajr": 10},
  "theme": {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "24h-seconds",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable"
  }
}
```

## `POST /api/auth/setup`

First-boot only: allowed while no admin exists, otherwise 409. Creates the admin and issues a session cookie. Rate limited to 5/min/IP.

```json
{"password": "password123"}
```

```json
{"ok": true}
```

Short passwords are 422, a second setup is 409, and the 6th attempt inside the window is 429.

## `POST /api/auth/login`

Password-only admin login. Issues a session cookie on success. Rate limited to 5/min/IP; wrong passwords are 401.

```json
{"password": "password123"}
```

```json
{"ok": true}
```

## `POST /api/auth/logout`

Clears the session cookie. Idempotent: always 200, even without a session.

```json
{"ok": true}
```

## `GET /api/auth/session`

Session status plus whether first-boot setup is still required. No auth required.

```json
{"authenticated": true, "setup_required": false}
```

## `POST /api/playlists`

Admin session required. Create a playlist; the `id` is server-generated when the body omits it (`null`). Windows are `HH:MM` clock bounds (each end optionally a marker name); `anchor_marker` must name a Prayer Time Marker, never a boundary. More than 50 items is 422. The response is the stored playlist.

```json
{
  "id": null,
  "title": "Evening Reminders",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

```json
{
  "id": "p1",
  "title": "Title p1",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

## `GET /api/playlists`

Admin session required. Lists every playlist with ordered items as a `playlists` envelope; each entry has the `POST /api/playlists` response shape (see `api/fixtures/playlist.json`).

## `GET /api/playlists/preview`

Admin session required. Server-side Stage preview computed over the Task 5 occupancy engine: `stage` is the current Stage id (`clock`, `countdown:adhan:<prayer>`, `countdown:iqamah:<prayer>`, `playlist:<id>`), and each entry reports `on_stage_now` plus the next 5-minute sample in the coming 24h at which it would hold the Stage (`next_at`, `null` when never in-window). 503 before setup, 404 without a schedule.

## `GET /api/playlists/{playlist_id}`

Admin session required. Returns one playlist with ordered items; unknown ids are 404.

```json
{
  "id": "p1",
  "title": "Title p1",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

## `PUT /api/playlists/{playlist_id}`

Admin session required. Full-replace body; the path id and body id must match (else 422), unknown ids are 404. Request and response share the playlist shape.

```json
{
  "id": "p1",
  "title": "Title p1",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

```json
{
  "id": "p1",
  "title": "Title p1",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

## `PATCH /api/playlists/{playlist_id}`

Admin session required. Flips one playlist's active flag without touching its items; unknown ids are 404.

```json
{"active": false}
```

```json
{
  "id": "p1",
  "title": "Title p1",
  "active": true,
  "window_start": "09:00",
  "window_end": "18:00",
  "anchor_marker": null,
  "anchor_start_offset_min": 0,
  "anchor_stop_offset_min": 0,
  "cycle_mode": "indefinite",
  "max_cycles": null,
  "items": [
    {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
  ]
}
```

## `DELETE /api/playlists/{playlist_id}`

Admin session required. Deletes a playlist; its items cascade. Unknown ids are 404; success returns an `ok` envelope.

## `POST /api/playlists/{playlist_id}/items`

Admin session required. Stores one uploaded image (base64 JSON, 5MB cap, JPG/PNG/WebP with EXIF stripped via the shared image store) and appends it to the playlist items; a full 50-item playlist is 422. The response echoes the new item slot.

```json
{"image_base64": "aGVsbG8=", "duration_s": 7}
```

```json
{"image_path": "a.jpg", "duration_s": 10, "sort_order": 0}
```

## `DELETE /api/playlists/{playlist_id}/items/{sort_order}`

Admin session required. Removes the item at one sort position, keeping the rest in place. Unknown playlists and unknown positions are 404; success returns an `ok` envelope.

## `GET /api/displays`

Admin session required. Lists registered displays with effective theme and dim plus groups: each display carries its group dim override (or the settings default) and a `dim_source` of `group` or `settings`.

## `POST /api/displays`

Admin session required. Registers one display against an existing group; duplicate ids are 409, unknown groups are 422.

```json
{"id": "hall-1", "name": "Main Hall", "group_name": "Default"}
```

## `PATCH /api/displays/{display_id}`

Admin session required. Sets per-display overrides (theme choice, group assignment); empty bodies are 422, unknown displays and unknown groups are 404/422. An explicit `group_name` null clears the assignment (the effective dim falls back to settings); an explicit `current_theme` null is not an update, so a null-theme-only body is 422.

```json
{"current_theme": "midnight", "group_name": null}
```

## `PATCH /api/display-groups/{name}`

Admin session required. Sets group overrides (theme default, dim minutes 5–60, carousel flag); unknown groups are 404.

```json
{"theme": "midnight", "dim_minutes_override": 30, "carousel_enabled": false}
```

## Errors

Unknown schedules are 404 with a detail message — including a `zone` that is not the configured zone, even when calc coordinates are set. Unconfigured installations are 503 with a detail message. Invalid bodies and query inputs are 422; `PUT /api/settings` rejects (422) bodies that duplicate a prayer's iqamah rule, omit a prayer's rule, set a `fixed` rule without `fixed_time`, set `delay_minutes` outside 0–60, or set a theme knob outside its closed enum, leaving the stored settings unchanged. A `fixed` iqamah time at or before its adhan is stored but resolves to 503 (`ConfigError`) on schedule reads until corrected. Missing admin sessions are 401. Exhausted login or setup rate limits are 429. Documentation endpoints are 404 off-LAN and 401 on-LAN without a session.

## Versioning
Additive fields allowed without bump — e.g. `time_synced` on `next-event` and SSE `state` payloads (slice 1A-8), and `stage` on SSE `tick` payloads. Renames/removals/semantic changes require `/api/v2/...` + fixtures + changelog + migration note.

Pre-consumer amendments: before the first frontend consumer lands (1B-1), semantic corrections may amend v1 fixtures + this document in place with a CHANGELOG migration note instead of standing up `/api/v2`; slice 1A-4a is exercised under this clause.
