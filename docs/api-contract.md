# API Contract (normative)

> File-config history note: the served surface is read-only —
> `prayer-day`, `next-event`, `events` (SSE), `version`, and `/display`.
> Every admin/database section below (`settings` writes, `manual-day`,
> `auth`, `playlists` CRUD, `adhan-audio` uploads, `displays`/`display-groups`,
> `backup`, `logs`) documents the pre-file-config API and now answers
> 404. Those sections are retained as documentation history (the parity
> tests map only the kept routes); fixtures win on conflict, and CI
> enforces parity for the kept surface.

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

Events: `state` (on transition), `tick` (1/min heartbeat with server `now` + Main Stage id `stage`; the display reloads when `stage` changes), `config-update` (settings/theme/playlist changed → refetch). `state` payloads always carry `time_synced` (FR-1.6) and carry `next_boundary`/`boundary_at` when the opt-in is on (see sample). `stage` is one of `clock`, `countdown:adhan:<prayer>`, `countdown:iqamah:<prayer>`, `playlist:<id>`, or `error` (config/schedule failure — the display reloads into the route slate instead of rendering a healthy Clock). `60s` poll of `next-event` is the fallback. Sample in `api/fixtures/events-stream.txt`. OpenAPI documents the three payload schemas inline as an `anyOf` (under `type: object`) beneath the `text/event-stream` content.

## `GET /api/version`

```json
{"version": "0.1.0", "api": "v1"}
```

## `GET /api/settings`

Admin session required. Full installation settings including the boundary offsets (imsak 0–10 default 10 with 0 hiding imsak on display; dhuha 15–30 default 28), the calculation `method` (`MABIMS`/`MWL`/`ISNA`/`Egyptian`) plus `asr_juristic` (`shafi`/`hanafi`, default `shafi`), the `boundary_countdown` opt-in and `calc_only` offline mode, the pre-adhan Stage-takeover window (global default 5 min, range 0–90, with optional per-prayer overrides keyed by prayer name; absent prayer = default), the `timezone` IANA device-clock zone (e.g. `Asia/Kuala_Lumpur`, default `Asia/Kuala_Lumpur`; changing it requires a service restart to take effect), and the `theme` knobs (closed enums: `palette` ∈ `classic-green|midnight|sand`, `font` ∈ `outfit|system`, `countdown_style` ∈ `boxes|inline`, `clock_format` ∈ `24h|24h-seconds|12h`, `hijri_form` ∈ `long|short`, `boundary_strip` ∈ `show|hide`, `density` ∈ `comfortable|compact`; anything else is 422). Per-display `display_settings` rows (`theme.*` plus `dim_minutes_override` 5–60) override the knobs and dim for one `GET /display?id=` render. Adhan audio is additive and silent by default: `adhan_audio_enabled` (default `false`), `adhan_volume` 0–100 (default 70), `quiet_hours_start`/`quiet_hours_end` (`HH:MM`, both-or-neither; overnight wrap allowed, e.g. `22:00`–`06:00` quiets when the current time is at or past start or before end), and `adhan_muted_prayers` (prayer-name list, default `[]`).

```json
{
  "masjid_name": "Masjid Test",
  "zone": "SGR01",
  "hijri_offset": 0,
  "imsak_offset_min": 10,
  "adhan_duration_s": 180,
  "adhan_audio_enabled": false,
  "adhan_volume": 70,
  "adhan_muted_prayers": [],
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
  "quiet_hours_start": null,
  "quiet_hours_end": null,
  "theme": {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "12h",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable"
  },
  "timezone": "Asia/Kuala_Lumpur"
}
```

## `PUT /api/settings`

Admin session required. Full-replace body; the response echoes the stored settings. The adhan-audio fields ride the same full-replace body (see `GET /api/settings`). A successful write publishes a `config-update` event with the `settings` group. `timezone` is an IANA zone name (default `Asia/Kuala_Lumpur`); a changed timezone takes effect on service restart.

```json
{
  "masjid_name": "Masjid Test",
  "zone": "SGR01",
  "hijri_offset": 0,
  "imsak_offset_min": 10,
  "adhan_duration_s": 180,
  "adhan_audio_enabled": false,
  "adhan_volume": 70,
  "adhan_muted_prayers": [],
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
  "quiet_hours_start": null,
  "quiet_hours_end": null,
  "theme": {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "12h",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable"
  },
  "timezone": "Asia/Kuala_Lumpur"
}
```

## `PUT /api/manual-day`

Admin session required. Pin one day's manual schedule (full 8-marker `HH:MM` body; `date` is `YYYY-MM-DD`). The request carries no zone — the day is stamped with `settings.zone` server-side, and a second PUT for the same date replaces the pin. Times must satisfy `Imsak < Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha`, else 422. The response echoes the pinned day in the `GET /api/prayer-day` shape with `"source": "manual"` and `"stale": true`. Precedence is structural: a manual pin outranks automatic sources (manual > JAKIM > calc) and the daily sync never overwrites it; deleting the pin re-exposes the date to the next sync.

```json
{"asr": "15:30", "date": "2025-10-20", "dhuha": "07:25", "dhuhr": "12:15", "fajr": "05:45", "imsak": "05:35", "isha": "19:25", "maghrib": "18:05", "syuruq": "06:55"}
```

## `DELETE /api/manual-day?date=YYYY-MM-DD`

Admin session required. Release one day's manual pin; success returns an `ok` envelope and the date falls back to the automatic schedule. Dates with no manual pin are 404.

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

Admin session required. Create a playlist; the `id` is server-generated when the body omits it (`null`). Windows are `HH:MM` clock bounds (each end optionally a marker name); `anchor_marker` must name a Prayer Time Marker, never a boundary. More than 50 items is 422.

Cycle modes (`cycle_mode` ∈ `indefinite|repeat`, default `indefinite`):

| `cycle_mode` | `max_cycles` | Behaviour |
| :--- | :--- | :--- |
| `indefinite` | must be `null` | Loops forever; the Stage never releases on count. |
| `repeat` | required, `>= 1` | Releases the Stage after N full passes. |

Illegal pairings (`repeat` without `max_cycles`, `indefinite` with non-`null` `max_cycles`) are 422.

Items are image-only in v1.0: each item carries an `image_path` only — no video/audio items. New images arrive via `POST /api/playlists/{playlist_id}/items` (JPG/PNG/WebP, 5MB cap, EXIF stripped, max 50 items per playlist). The response is the stored playlist.

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

Admin session required. Server-side Stage preview computed over the Task 5 occupancy engine: `stage` is the current Stage id (`clock`, `countdown:adhan:<prayer>`, `countdown:iqamah:<prayer>`, `playlist:<id>`), and each entry reports `on_stage_now` plus the next 5-minute sample in the coming 24h at which it would hold the Stage (`next_at`, `null` when never in-window). 503 before setup, 404 without a schedule. The playlist-editor page embeds the same payload shape; when the preview cannot resolve (missing config or schedule) the embedded payload carries an additive `error` key with the reason (`stage` reads `error`, `playlists` is empty) instead of silent null, and the page renders the reason in the preview slot.

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

## `POST /api/adhan-audio`

Admin session required. Stores one adhan MP3 (base64 JSON, 10MB cap, MP3 magic only: `ID3` header or MPEG frame sync) under the canonical `adhan.mp3` name; uploads replace each other so the display URL stays stable. Invalid base64 and non-MP3 bytes are 400, payloads over 10MB are 413. Success is 201 with a `file`/`size` envelope (`{"file": "adhan.mp3", "size": 10}` for the sample below). The file is served via the existing `/static` mount at `/static/uploads/adhan.mp3` (custom `media_dir` deployments keep working iff the dir stays under the static root). The display plays it during the ADHAN overlay only (kiosk Chromium runs with no-gesture autoplay, so no user gesture is needed); every other state stays silent.

```json
{"audio_base64":"SUQzBAAAAAAAAA=="}
```

## `DELETE /api/adhan-audio`

Admin session required. Removes the adhan MP3; idempotent (a missing file is still 200 with an `ok` envelope).

## `GET /api/displays`

Admin session required. Lists configured displays with effective theme and dim plus groups: each display carries its group dim override (or the settings default) and a `dim_source` of `group` or `settings`. Display identity is the screen URL id (`GET /display?id=...`) — there is no registration step.

## `PATCH /api/displays/{display_id}`

Admin session required. Sets per-display overrides (name, theme choice, group assignment), creating the display row when the id is unknown; empty bodies are 422, unknown groups are 422. An explicit `group_name` null clears the assignment (the effective dim falls back to settings); an explicit `current_theme` null is not an update, so a null-theme-only body is 422.

```json
{"name": null, "current_theme": "midnight", "group_name": null}
```

## `PATCH /api/display-groups/{name}`

Admin session required. Sets group overrides (theme default, dim minutes 5–60, carousel flag); unknown groups are 404. `carousel_enabled` (default on) gates the `#carousel-dot` footer indicator on `GET /display?id=` for displays in the group — the footer itself still renders NORMAL-only (FR-3.3 pause rule), so the flag only toggles the dot. Unknown display ids render the global default (carousel on).

Override scope is theme+dim-only by design: groups pin presentation (theme default, dim minutes, carousel flag), and per-display rows allow only the `theme.*` knobs plus `dim_minutes_override` — schedule, iqamah rules, and countdown windows are never forked per group or display.

```json
{"theme": "midnight", "dim_minutes_override": 30, "carousel_enabled": false}
```

## `POST /api/backup/export`

Admin session required. Downloads the whole installation as one zip: the `muhideen.db` snapshot at the zip root plus the uploads tree under `media/`. The response is `application/zip` (not JSON, so no fixture) with an attachment filename of the form `muhideen-backup-<YYYYMMDD-HHMMSS>.zip`. The archive contains password hashes (`users` table) — treat the download as secret, with the same handling as the DB file itself. Defense-in-depth caps: total archive ≤256MB, per-member ≤64MB, member count ≤512 (media uploads are already capped upstream). Symlinks in the media tree are skipped on export (never followed); symlink members in an imported archive are rejected.

## `POST /api/backup/restore`

Admin session required. Replaces the installation from a base64 backup zip (base64 JSON — no multipart parser on the offline-first footprint, mirroring the playlist/adhan uploads). The staged database is migrated before it replaces the live one (older versions migrate up; a backup from a newer application version than the installed build is rejected); the media tree swaps atomically; no restart is required. The pre-decode length bound mirrors the adhan route (payloads over ~341MB of base64 are 413); invalid base64 and bundle-validation failures (non-zip bytes, corrupt or encrypted members, traversal entries, aliased path segments, symlink or duplicate members, missing `muhideen.db`, oversize members) are 400. Success returns an `ok` envelope. Request shape matches `api/fixtures/backup-restore.json` (`request` key; `response` key is the `ok` envelope).

```json
{"archive_base64":"UEsDBA=="}
```

## `GET /api/logs`

Admin session required. Tails the `muhideen` systemd unit's journal (`journalctl -u muhideen --quiet --output=json -n <lines>`); `lines` counts journal entries (one JSON object per entry, so an entry with embedded newlines is a single line) and defaults to 100, clamped to 1..1000 by the query validator (out-of-range values are 422). With a journal the response carries the entry messages array (`{"available": true, "lines": [...]}`); without one (dev machines, failing probes, empty journal) it carries `{"available": false, "hint": "journalctl ..."}` — never a 500 for absent logs. Both shapes are pinned in `api/fixtures/logs.json` (`available`/`unavailable` keys).

## Errors

Unknown schedules are 404 with a detail message — including a `zone` that is not the configured zone, even when calc coordinates are set. Missing or invalid configuration is 503: the wire detail is path-scrubbed (`config: …`; the full path goes to the server log), while domain causes (e.g. a `fixed` iqamah time at or before its adhan) pass through verbatim. Invalid bodies and query inputs are 422; naive `now` values are 422. `/docs`, `/redoc`, and `/openapi.json` are public by decision (read-only schemas only). The admin/database error codes (401 sessions, 429 rate limits) no longer exist on this surface.

## Versioning
Additive fields allowed without bump — e.g. `time_synced` on `next-event` and SSE `state` payloads (slice 1A-8), and `stage` on SSE `tick` payloads. Renames/removals/semantic changes require `/api/v2/...` + fixtures + changelog + migration note.

Pre-consumer amendments: before the first frontend consumer lands (1B-1), semantic corrections may amend v1 fixtures + this document in place with a CHANGELOG migration note instead of standing up `/api/v2`; slice 1A-4a is exercised under this clause.
