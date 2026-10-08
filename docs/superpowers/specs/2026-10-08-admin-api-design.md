# Admin API surface — design (Option A: token-gated file-config PATCH)

Date: 2026-10-08. Status: brainstorm-approved (§§1–4), pending spec review.
Goal: full FastAPI surface so another developer can build a frontend admin panel, without undoing the file-config decision (no DB/users/sessions).

## Context

- Current backend is read-only: `GET /api/prayer-day|next-event|events|version` + `GET /display` (`src/muhideen/api/app.py:1-7`). Config lives in `config/muhideen.json` (`ConfigFile` in `adapters/file_models.py`), cache in `prayer_buffer.json`, binaries in `media/`. `ConfigWatcher` hot-reloads + SSE `config-update`.
- `FileSettingsRepo.save` (`adapters/file_config.py:312`) already does raw-dict surgery + `ConfigFile.model_validate` + `_atomic_write_json` (`file_config.py:179`). `FilePlaylistRepo` is read-only by intent (`file_config.py:907`); displays have no repo (read inline in `display()` route).
- Historic session-auth + SQLite surface (`docs/api-contract.md:63-429`, `PRD.md:215,316-317`) was removed; `tests/test_no_admin.py:75-118` pins the removal. This design does not restore it.

## Agreed decisions

1. Auth: static API token, Bearer, file outside `muhideen.json`.
2. Scope: full `muhideen.json` CRUD (all sections + playlists/displays/manual-days).
3. Media: multipart upload API (not path-strings-only, not base64-in-JSON).
4. Update style: per-section PATCH (not single full-file PUT).

## §1 Auth + cross-cutting

- New CLI flag `--admin-token-file` (default `./config/admin_token`), argparse-only per `service.py` precedent (no env magic). File holds one line `secrets.token_urlsafe(32)`, mode `0600`. Missing/empty → writes answer `503 {detail: "admin writes disabled"}`, reads stay public. `install.sh` generates once if absent; rotation = rewrite + restart. Token never appears in `GET /config`.
- Gate `PATCH/POST/PUT/DELETE` on `/api/config/*`, `/api/playlists/*`, `/api/displays/*`, `/api/media/*`, `/api/backup/restore` with `Authorization: Bearer` + `secrets.compare_digest`; fail `401 {detail}`. No sessions, no login rate-limit v1.
- Reads stay public (`prayer-day`, `next-event`, `events`, `version`, `display`, `GET /api/config*`). OpenAPI gains `HTTPBearer` securityScheme; `/docs` stays public (same rationale as `app.py:654-659`).
- Write semantics: shared in-process `ConfigWriteLock` (extend `FileSettingsRepo._lock` pattern) + `_atomic_write_json` tmp+rename+fsync. Validate-before-rename (`ConfigFile.model_validate` + `validate_pins`); invalid never touches disk, last-good keeps serving. Success publishes existing `event_bus.publish("config-update", (...))` — no new SSE channel.
- Errors, all JSON `{detail}` (paths scrubbed via existing `_public_detail`): `401` token, `422` validation `{detail:[{loc,msg}]}`, `404` unknown id/date, `409` duplicate id/date, `503` writes-disabled/broken-config.

## §2 Per-section config PATCH

PATCH bodies are all-optional partials; omitted = unchanged, explicit `null` clears where nullable; each preserves `displays`/`playlists`/`manual_days` byte-for-byte (existing save-surgery pattern).

- `GET /api/config` (full file, export source) + `GET /api/config/{masjid,schedule,timing,theme,adhan-audio}`.
- `PATCH /api/config/masjid`: `{name? 1-200, timezone? IANA}`. Tz change = restart-required (existing `_on_reload` warns, keeps old clock).
- `PATCH /api/config/schedule`: `{sync_provider?, zone?, jakim:{zone?}?, aladhan:{base_url?,method?}?, method? MABIMS|MWL|ISNA|Egyptian, asr_juristic? shafi|hanafi, lat?, lon?, calc_only?, hijri_offset? -2..2, imsak_offset_min? 0-10, dhuha_offset_min? 15-30, boundary_countdown?}`. `lat`+`lon` paired-or-null; `jakim.zone` required iff `jakim`; `lat/lon` required iff `aladhan`; provider/base_url/method switch = restart-required (client built once); zone/coords/offsets hot-reload. `manual_days` excluded (see §3).
- `PATCH /api/config/timing`: `{adhan_duration_s? >0, dim_minutes_default? 5-60, dim_minutes_jumuah? 5-60, countdown_before_adhan_min? 0-90, countdown_before_adhan_overrides?{}, iqamah_rules?[]}`. `iqamah_rules` when present = full 6 (`fajr,dhuhr,asr,maghrib,isha,jumuah`; `delay 0-60` / `fixed HH:MM`).
- `PATCH /api/config/theme`: 7 enums all-optional (`palette, font, countdown_style, clock_format, hijri_form, boundary_strip, density`).
- `PATCH /api/config/adhan-audio`: `{enabled?, volume? 0-100, quiet_hours_start/end? paired HH:MM|null, muted_prayers?[] prayer-only, file?}`. `file` is path-string only; binaries via §4. File validated only when `enabled` (`resolve_adhan_path` semantics).

## §3 Resource CRUD

- Playlists: `GET /api/playlists`, `POST /api/playlists`, `GET/PATCH/DELETE /api/playlists/{id}`; items `POST /api/playlists/{id}/items`, `DELETE /api/playlists/{id}/items/{sort}`. Constraints: `0-50` image-only items, `window_*` = `HH:MM`/marker/`null`, `anchor_marker` prayer-only, `max_cycles` null-iff-`indefinite`. Plus `GET /api/playlists/preview?moment=<tz-aware ISO>` → `{stage, active_playlists[]}` via `resolve_stage` for window testing.
- Displays: `GET /api/displays`, `PUT /api/displays/{id}` (upsert), `PATCH /api/displays/{id}`, `DELETE /api/displays/{id}`. Body `{name?, language? en|ms|ar|bm, theme? partial-7-null=inherits, dim_minutes_override? 5-60|null, carousel_enabled?}`. No schedule/iqamah fork.
- Manual-days: `GET /api/config/manual-days`, `PUT /api/config/manual-days/{date}` (≥1 of 8 `HH:MM` markers), `DELETE` same. If `manual_days_file` ref set, writes route to pins file via `resolve_pins_path`; inline+file stays exclusive. Partial pins validated by `validate_pins` (needs buffer row to complete against, else `422`).

## §4 Media + validate + ops

- Media: `GET /api/media` (`[{path,size}]`), `POST /api/media` (`multipart file, kind=adhan|image`), `DELETE /api/media/{path}`. Media-relative only, `..`/absolute/`?`/`#` rejected; images `jpg|png|webp ≤5MB` Pillow re-encode (strip EXIF), audio `mp3 ≤10MB` magic-byte check; `media/` prefix stripped. Boot + upload `mkdir -p media_dir` so the `/media` mount never needs a restart (fixes `app.py:668` gap).
- Validate: `POST /api/config/validate` (full-file candidate) + `POST /api/config/manual-days/validate` (pins array) → `200 {ok:true}` or `422` same shape as PATCH, nothing written.
- Ops: `GET /api/version` (exists); `GET /api/backup/export` (zip: `muhideen.json` + pins + `prayer_buffer.json` + media manifest/binaries, size-capped); `POST /api/backup/restore` (multipart zip → validate-all → atomic swap); `GET /api/logs?lines=200` (journalctl tail, fail-soft `501` on compose). Frontend subscribes to existing `/api/events` (`config-update`) + 60s `next-event` poll fallback — no new realtime channel.
- Frontend ergonomics: per-endpoint `422` examples in OpenAPI; `api/fixtures/admin-*.json` + extended `tools/mock_api.py` so UI builds without a Pi.

## Out of scope v1

Per-user accounts/audit, login rate-limit, `docs` gating, video playlist items, per-display schedule fork, OTA update trigger.
