# Muhideen — Phases and Slices (Eagle's Eye)

Go-to reference before writing any detailed implementation plan. A detailed plan is required per slice before code. This document defines the slices, their order, and their done-criteria — it never contains implementation itself.

Sources of truth: `PRD.md` (requirements), `ARCHITECTURE.md` (layers), `docs/api-contract.md` + `api/fixtures/` (contract), `TESTING_STRATEGY.md` (gates), `docs/adr/` (locked decisions).

## How to Use This Document

1. Pick the next unlanded slice in phase order (§1–§4). Never skip entry criteria.
2. Write a detailed plan for that slice only: context, contract diff (or `none`), domain changes, adapter changes, tests per `TESTING_STRATEGY.md`, purity/import-linter impact, fixture/changelog updates, rollback notes.
3. Land the slice only when all exit criteria pass, including the full `uv` quality gate.
4. Frontend tracks build against fixtures from the moment slice 1A-3 lands — no waiting for DB, JAKIM, or Pi hardware.

## Principles (Non-Negotiable)

* Future fluidity over small diffs. Solid core from day 0.
* Single repo, single deployable. Logical backend/frontend split only.
* Backend-first, frontend-independent via versioned contract + normative fixtures.
* Time is injected (`Clock` port). Domain is pure `(now, schedule, settings)`.
* DX/CX is an NFR: frontend-only needs browser + `uv run tools/mock_api.py`; backend-only needs `uv sync` + `uv run pytest`.
* Two marker classes (PRD FR-1.7): Prayer Time Markers (5 + Jumuah) alone carry adhan/iqamah/dim/state; Boundary Time Markers (imsak/syuruq/dhuha) are informational — opt-in countdown, secondary render level, and they are tested as pointer behavior, never as state windows.

## Phase 0 — Foundations (mostly landed)

| Slice | Goal | Exit |
|---|---|---|
| 0A Docs | PRD, ARCHITECTURE, CONTEXT, TESTING_STRATEGY, CONTRIBUTING, ADRs 0001–0003, api-contract + fixtures | This repo state |
| 0B Toolchain | `pyproject.toml` (hatchling, `>=3.11`, strict pyright, ruff 88, import-linter, coverage 95), `uv.lock`, CI running the full gate | `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest` green on empty skeleton |
| 0C Skeleton | `src/muhideen/{core,domain,engine,adapters,api,views,migrations}` + `py.typed`, `tests/{unit,contract,integration,property,e2e}`, `tools/{mock_api,new_theme,lint_theme}`, `themes/classic-green` scaffold | Imports resolve, mock_api serves fixtures, lint_theme passes |

## Phase 1A — Backend Core (backend track, blocks everything)

Strict order. Each slice's exit unblocks named frontend work.

| Slice | Goal | Entry | Exit | Unblocks |
|---|---|---|---|---|
| 1A-1 Core vocab + ports | `core/`: frozen value objects (PrayerDay, NextEvent, IqamahRule, Settings), ports (`PrayerRepo`, `SettingsRepo`, `JAKIMClient`, `CalcEngine`, `Clock`, `EventBus`, `MediaStore` — plus `DisplayRepo` in 1A-5, `UserRepo` in 1A-7, `TimeSyncProbe` in 1A-8), errors | 0C green | Unit tests: immutability, equality, hashability; pyright strict; import-linter green | Nothing yet (foundation) |
| 1A-2 Domain pure logic | `domain/`: state machine (NORMAL→PRE_ADHAN→ADHAN→IQAMAH_COUNTDOWN→SALAH_DIM, Syuruq/Jumuah/midnight rules), fallback chain, iqamah resolution, Hijri offset, freshness flags. `FakeClock` + in-memory fakes | 1A-1 | `unit` green incl. pinned-time determinism test (same now+snapshot+settings → identical output); purity scan: no `fastapi/sqlite3/httpx/datetime.now` in `domain/` | Frontend logic preview via fixtures review |
| 1A-3 Executable contract | Pydantic DTOs for `prayer-day`, `next-event`, SSE events, heartbeat, version. `tests/contract/` enforces DTO ↔ `api/fixtures/` ↔ OpenAPI parity | 1A-2 | `contract` green; any drift fails CI; `docs/api-contract.md` + fixtures updated together | **Frontend starts (1B-1/1B-2 against fixtures)** |
| 1A-4 Engine orchestration | `engine/`: resolve day → compute next event → fan-out. No HTTP, no SQL | 1A-3 | `integration` with fake repos green; property tests for no-overlap states, monotonic targets | Frontend state-transition QA |
| **1A-4a** Marker taxonomy correction | Correct all landed work (Phase 0–1A-4) to the two-class model: `core` `MarkerName`/`MarkerKind`, `PrayerDay` + Imsak/Dhuha, domain prayer-only windows + gated boundary pointer, contract `prayers`/`boundaries` split + narrowed `next_prayer` + `next_boundary`, PRD/CONTEXT/README/skills sweep | 1A-4 landed | full `uv` gate; contract parity green with amended fixtures; boundary markers proven state-free by unit + property tests; docs sweep complete | 1A-5 builds on corrected shapes |
| 1A-5 Persistence | `adapters/sqlite_repo` (WAL, single writer, 60s heartbeat batching), `migrations/*.sql` + `user_version`, `VACUUM INTO` backup path; schema per corrected PRD §6.2 (all 8 marker columns, `boundary_countdown` settings key, prayer-only `iqamah_rules`) | 1A-4a | `integration` on tmp-file SQLite green; migration upgrade/downgrade tested | Admin persistence work |
| 1A-6 Schedule sources | `adapters/jakim_esolat` (defensive client per PRD §6.1) + `calc_mabims` fallback; library choice recorded (spike first); scheduler 02:00 + backoff; defensive client parses all 8 markers with adapter-side naming map (source spellings → `MarkerName`), ordering validation `Imsak<Fajr<Syuruq<Dhuha<Dhuhr<Asr<Maghrib<Isha`, calc fallback derives Boundary markers per recorded research | 1A-5 | Parser-rejection, ordering-validation, retry, and stale-flag tests green; no network in unit/contract | Offline-first claim true |
| 1A-7 HTTP surface | FastAPI `def` sync handlers, SSE stream, admin settings endpoints (expose the `boundary_countdown` opt-in), auth (Argon2id), rate limits, `/docs` LAN-gated | 1A-6 | `e2e` via ASGI client green; OpenAPI matches DTOs; auth/rate-limit tests green | Full-stack wiring |
| 1A-8 Device integration | `muhideen.service` unit, `install.sh` (`uv sync --offline` + seed fetch + preflight), `update.sh`, NTP/RTC health + `TIME UNSYNCED` path | 1A-7 | Install on Pi 4 green; backend ready ≤10s; render path exercised without Chromium | Kiosk bring-up |

## Phase 1B — Frontend Against Fixtures (frontend track, parallel from 1A-3)

| Slice | Goal | Entry | Exit |
|---|---|---|---|
| 1B-1 Display `classic-green` | Match `preview.jpg` density: header, hero clock + next-prayer (always a Prayer Time Marker), **5 prayer cards + a secondary Boundary Time Marker strip (Imsak/Syuruq/Dhuha)**, footer. Monotonic tick + SSE resync + 60s poll fallback. DTOs only; boundary countdown rendered only when `next_boundary` is present | 1A-4a fixtures | Renders from `mock_api` :8001; 10m legibility + AA contrast checked; no `domain/` imports |
| 1B-2 States + dim | PRE_ADHAN hide, ADHAN overlay, IQAMAH countdown, SALAH_DIM blackout/minimal clock, admin skip; boundary markers verified never to trigger PRE_ADHAN/ADHAN (events-stream scenarios include the gated pointer) | 1A-4 semantics | All 5 states + Jumuah/boundary-marker edges visually verified against `events-stream.txt` scenarios |
| 1B-3 Admin wizard | Setup (name, zone/latlon, mode, password, hijri offset), settings API thin client, QR (`muhideen.local`, one-time token, hidden during prayer); wizard uses same settings API as later screens (no fork), incl. boundary-countdown opt-in on the same settings API | 1A-7 | Mobile-width pass; wizard uses same settings API as later screens (no fork) |

## Phase 2 — Content and Management

| Slice | Goal | Depends |
|---|---|---|
| 2A Carousel manager | Upload/re-encode (Pillow, EXIF strip, 5MB/50-item caps), ordering, toggle, pause rule | 1A-5, 1B-1 |
| 2B Theme pipeline | Zip validation (allowlist, no symlinks, 2MB), sandbox iframe + CSP + `postMessage` JSON, preview-then-publish atomic per group | 1B-1, 1A-7 |
| 2C Backup/restore + logs | `sqlite+media` export/import, journald view, version footer | 1A-5 |
| 2D Audio | Admin-uploaded chime only, per-Prayer-Time-Marker enable (chimes follow Adhan, which only Prayer Time Markers have), volume + quiet hours | 1B-2 |
| 2E Extra themes | `minimal-dark`, `info-board` via `new_theme.py` | 2B |

## Phase 3 — Multi-Display and Appliance Polish

| Slice | Goal | Depends |
|---|---|---|
| 3A Groups + targeting | Group CRUD, per-group theme/carousel/dim override, global defaults | 1A-5, 1B-1 |
| 3B CEC power | `cec-utils` on/off windows, master toggle, per-group opt-out, never off mid-admin | 1A-8 |
| 3C Community + thin client | Theme library docs, Zero 2 W thin-client image (display-only vs headless-server) | 2B |
| 3D Go appliance eval | Drop-in binary behind frozen contract; decision recorded, no display/theme changes | 1A-3 contract stable |

## Explicitly Out of Scope (MVP)

Video carousel, cloud dashboard, prayer-request messaging, zakat/khutbah CMS.

## Detailed-Plan Checklist (per slice)

Copy into each slice plan: (1) PRD refs, (2) contract diff or `none` + fixture updates, (3) domain/adapter/API/view changes with ownership, (4) tests by layer + new invariants, (5) purity/import-linter impact, (6) docs touched (CONTEXT glossary only if terms change; ADR only if hard-to-reverse), (7) rollback (migration downgrade / contract version bump), (8) exit gate command output.
