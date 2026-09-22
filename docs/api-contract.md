# API Contract (normative)

Single-repo logical split. Backend implements first; frontend builds against `api/fixtures/`. Any example here duplicated in fixtures — fixtures win on conflict, and CI enforces parity.

Base URL (device): `http://muhideen.local:8000`. Mock: `http://localhost:8001` via `uv run tools/mock_api.py`.

## `GET /api/prayer-day?date=YYYY-MM-DD&zone=SGR01`

Day schedule + freshness. `source` is per-day fallback provenance; `stale` drives the banner. `prayers` holds the 5 Prayer Time Markers (cards/hero level); `boundaries` holds Imsak/Syuruq/Dhuha — rendered at secondary level, never adhan/iqamah/dim.

```json
{
  "date": "2025-10-20",
  "zone": "SGR01",
  "prayers": {"fajr": "05:45", "dhuhr": "12:15", "asr": "15:30", "maghrib": "18:05", "isha": "19:25"},
  "boundaries": {"imsak": "05:35", "syuruq": "06:55", "dhuha": "07:25"},
  "source": "jakim",
  "stale": false
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
  "boundary_at": "2025-10-21T05:35:00+08:00"
}
```

`state` ∈ `NORMAL|PRE_ADHAN|ADHAN|IQAMAH_COUNTDOWN|SALAH_DIM`. `next_prayer` ∈ `fajr|dhuhr|asr|maghrib|isha|jumuah` only (Prayer Time Markers; never a Boundary Time Marker). `next_boundary`/`boundary_at` name the next Boundary Time Marker instant and are present only when the installation opts in (`boundary_countdown` setting), default off; they never influence `state`.

## `GET /api/events` (SSE `text/event-stream`)

Events: `state` (on transition), `tick` (1/min heartbeat with server `now`), `config-update` (settings/theme/carousel changed → refetch). `state` payloads carry `next_boundary`/`boundary_at` when the opt-in is on (see sample). `60s` poll of `next-event` is the fallback. Sample in `api/fixtures/events-stream.txt`.

## `POST /api/displays/heartbeat`

Request body:

```json
{"id": "HALL-01"}
```

Response body (`200 OK`):

```json
{"ok": true}
```

Server records `last_seen`/IP/group server-side in 60s batches. No auth; LAN-only; IDs pre-registered or pending-approval.

## `GET /api/version`

```json
{"version": "0.1.0", "api": "v1"}
```

## Versioning
Additive fields allowed without bump. Renames/removals/semantic changes require `/api/v2/...` + fixtures + changelog + migration note.

Pre-consumer amendments: before the first frontend consumer lands (1B-1), semantic corrections may amend v1 fixtures + this document in place with a CHANGELOG migration note instead of standing up `/api/v2`; slice 1A-4a is exercised under this clause.
