# Phase 1B — Frontend Against Fixtures (Design Spec)

Status: design approved section-by-section (§1–§7) in brainstorming, 2026-09-25.
Covers slices 1B-1 (display `classic-green`), 1B-2 (states + dim), 1B-3 (admin wizard).
Single spec for the whole phase (locked decision).

Sources of truth: `PRD.md`, `ARCHITECTURE.md`, `docs/api-contract.md` + `api/fixtures/`,
`TESTING_STRATEGY.md`, `docs/adr/`. This note is ephemeral planning per
`docs/development/AGENTS.md` and is not referenced from shipped code or docs.

## 1. Goal and non-goals

Goal: a Jama'ah-legible public display at PRD §0 density plus a minimal admin
surface, both built against the frozen v1 contract and served by the landed 1A
backend. Frontend-only contributors stay on browser + `mock_api` :8001; no DB,
no JAKIM, no Pi hardware required.

Non-goals (deferred, not designed here): theme framework / sandbox / extra
themes (2B/2E, per-group selection 3A), i18n packs (seam only), audio upload
(2D), carousel manager (2A), backup/restore UI (2C), group overrides (3A), CEC.

## 2. PRD references

FR-2.1 (dashboard: clock, Gregorian+Hijri, 5 cards + secondary boundary strip,
trilingual, next-prayer highlight), FR-2.2 (adhan overlay, silent in 1B),
FR-2.3 (iqamah countdown, server target + monotonic tick + SSE resync),
FR-2.4 (dim/blackout + admin skip), FR-2.5 (720p/1080p/4K, 16:9 primary, 9:16
best-effort, 10m legibility, AA, no h-scroll), FR-2.6 (state machine; boundaries
never trigger state), FR-1.7 (two marker classes; `boundary_countdown` opt-in),
FR-6.1 (mobile-friendly admin, live-reload durations), FR-6.2 (wizard as thin
client over the same settings API, no fork), FR-6.3 (QR LAN-only, one-time
setup token, hidden during prayer), FR-1.5 (Hijri + offset), FR-1.6
(`TIME UNSYNCED` banner), FR-3.3 (carousel pause rule).

## 3. Architecture and ownership (locked §1)

- `views/display/` (new, frontend-owned): `display.html` Jinja template at
  `GET /display?id=<ID>` + context builder mapping DTOs to template context.
  Classic-green §0 density, layout option A (split hero: clock left,
  next-prayer right; 5 cards in a row; slim boundary strip; footer).
  Never imports `domain/`.
- `views/admin/` (new, frontend-owned): `wizard.html`, `settings.html`,
  `login.html` under `/admin/*`. Thin `fetch()` clients over the settings and
  auth APIs. No wizard-only settings path.
- `static/` (new, frontend-owned): `app.css` (hand-written vanilla, ~10KB
  target) + `app.js` (SSE subscribe, monotonic tick, 60s poll fallback,
  30s heartbeat). No build step, no Tailwind, no CDN (offline-first).
- `themes/classic-green/`: untouched; stays a minimal read-only consumer until
  the Phase 2 sandbox pipeline.
- Future theme seam (addendum, no code): data/sync logic (`app.js`) stays
  separate from presentation (template regions with stable IDs + CSS); future
  themes consume resolved DTO JSON read-only through the same seam `themes/*`
  already assumes. 2B/2E/3A add the framework and pickers without touching
  `domain/`/`engine/` or breaking the contract.

## 4. Contract diff

Additive only, no version bump (allowed per contract versioning rule):

- `GET /api/prayer-day` gains `hijri_date`: `"1447-04-28"` (Gregorian date
  converted via `hijridate`, `hijri_offset` already applied; `null` when outside
  the library's 1343–1500 AH range — never a 500).
- Fixture updates: `prayer-day.json` (+ golden Hijri for 2025-10-20/offset 0),
  `docs/api-contract.md`, CHANGELOG additive-field note.
- No other endpoint shapes change. Display/admin HTML routes are new but carry
  no JSON contract.

### Hijri computation (tiny backend pre-step, backend track)

- New dependency `hijridate` (verified v2.6.0, MIT, py>=3.10, zero runtime
  deps, Umm al-Qura; matches FR-1.5's Umm-al-Qura/tabular option).
- New `adapters/hijri_date.py` (imports `core` + `hijridate` only):
  `shifted = gregorian_date + timedelta(days=offset)` then
  `Gregorian(y, m, d).to_hijri()`. Absolute-day equivalence makes the Gregorian
  shift exactly equal to a Hijri-day shift, so no Hijri month arithmetic is
  needed and `domain.apply_hijri_offset` semantics are preserved.
- Wired at the DTO mapping for `prayer-day` (presentation-adjacent, behind the
  existing port/adapter seam). Vendored-wheel packaging must include the new
  wheel for offline `install.sh`.

## 5. Display rendering (1B-1, locked §2–§3)

Regions with stable IDs: `hdr` (masjid name, zone, Gregorian + Hijri dates,
STALE / TIME UNSYNCED / calc-fallback banners), `hero-clock` (≥12vh),
`hero-next` (next prayer name + time + iqamah countdown; always a Prayer Time
Marker), `cards` (5, each EN+BM+Arabic; next-prayer highlighted), `bounds`
(secondary strip Imsak/Syuruq/Dhuha — the imsak item is hidden when
`imsak_offset_min == 0`; countdown only when `next_boundary`
present), `ftr` (carousel dot, QR hint toggle).

Realtime: first paint server-rendered (usable with JS disabled at reduced
freshness). `EventSource` on `/api/events`: `state` → refetch `next-event` and
re-render; `tick` → re-anchor monotonic baseline; `config-update` → refetch
settings-dependent regions. Countdowns use `performance.now()` anchored to the
last server `now`; server targets are the only time inputs. EventSource failure
→ 60s `next-event` poll. `POST /api/displays/heartbeat` every 30s with `?id=`.
503/404 render an error slate, never blank. QR hidden during `ADHAN` /
`SALAH_DIM`; carousel + footer indicator hidden `PRE_ADHAN` through end of
`SALAH_DIM`.

## 6. States, dim, edges (1B-2, locked §4)

- `NORMAL`: full dashboard. `PRE_ADHAN`: dashboard minus carousel/footer
  indicator/QR + "preparing for \<prayer\>" note.
- `ADHAN`: fullscreen overlay (trilingual prayer name + duration progress),
  `adhan_duration_s` (default 180s). Silent in 1B; no dead audio controls.
- `IQAMAH_COUNTDOWN`: hero becomes the prominent live countdown to `iqamah_at`.
- `SALAH_DIM`: black screen, white bilingual prayer name ("Asr Prayer" + Arabic
  default), minimal clock underneath, thin animated top progress bar against
  server `dim_until` via the monotonic tick. Cards/strip/footer hidden.
- Admin skip: 3s long-press on the dim screen dismisses dim locally until the
  next `state` event. Explicit assumption: physical access = admin; no new
  endpoint, no contract change.
- Edges: Jumuah replaces Dhuhr Friday (own rule + 45-min dim); midnight
  crossover renders server-resolved next-day Fajr; boundary markers never leave
  `NORMAL` (events-stream scenario walkthrough); Jawi deferred.

## 7. Admin wizard and tunables (1B-3, locked §5)

Routes `/admin/setup` (gated by `setup_required`), `/admin/settings` (session
required, else login), `/admin/login` (429 messaging surfaced).
Wizard, one thing per screen, mobile-width, large touch targets: masjid name →
zone code **or** lat/lon + method (MABIMS default; online-JAKIM vs calc-only
toggle) → admin password (client-side length pre-check) → Hijri offset stepper
(−2…+2) → review → `POST /api/auth/setup` then `PUT /api/settings`.
`/admin/settings` exposes the minimal tunables on the same API (no fork): name,
zone/latlon+method, calc-only, Hijri offset, `imsak_offset_min`,
`dhuha_offset_min`, per-prayer (+Jumuah) iqamah delays, adhan duration, dim
durations, `boundary_countdown` opt-in. Full-replace `PUT` includes and
submits both offset fields on every save, with inline 422 display; success notes live reload via
`config-update`. QR block (`http://muhideen.local:8000/admin`, no secrets)
rendered server-side, shown on demand, hidden by default and during prayer
states; one-time setup token with expiry note; fallback IP alongside (mDNS may
not resolve). BM/EN toggle on admin UI; display strings stay trilingual.

Strings seam (future i18n): 1B hardcodes BM/EN (admin) and EN/BM/Arabic
(display) in one `strings` block per template; community language packs replace
them later. No i18n framework in 1B.

## 8. Tests

Automated (CI, no Chromium): ASGI e2e — `/display` region assertions; all 5
states + Jumuah-Friday + boundary opt-in on/off from pinned payloads;
`events-stream.txt` replay proving boundaries never leave NORMAL;
wizard→settings round-trip; `hijri_date` golden test; static checks (≥12vh /
≥3.5vh rules present, palette contrast ≥4.5:1 computed, no `views/`+`static/`
→ `domain/` imports via import-linter/purity extension). Coverage
`fail_under=95` holds.

Manual checklist (browser vs `mock_api` :8001, then live backend): 10m legibility
at 1080p; AA visual pass; 5-state walkthrough + Jumuah + boundary on/off; dim
skip + progress bar; mobile-width wizard; QR show/hide incl. hidden-during-
prayer; forced STALE / TIME UNSYNCED / calc banners; 720p/1080p/4K + 9:16
best-effort; no horizontal scroll.

## 9. Purity, docs, rollback, risks

Purity: `views/` → DTOs + `core` only (formalize convention in CI scan);
`adapters/hijri_date.py` → `core` + `hijridate`. No layer-contract change.
Docs: `docs/api-contract.md`, fixtures, CHANGELOG. No CONTEXT term changes, no
ADR (reversible dep + local UI behavior).
Rollback: additive field ignored by old clients; drop dep + field on trouble;
`views/` detachable by route; no DB migration.
Risks: hijridate range → nullable field; mDNS unreliability → fallback IP;
kiosk Chromium manual-only; long-press discoverability → admin footer hint.

## 10. Exit gate

Manual checklist signed off plus the full `uv` gate green:
`ruff check`, `ruff format --check`, `pyright`, `lint-imports`, `pytest`.
