# Product Requirement Document (PRD)
## Open-Source Masjid Digital Display System (*Muhideen*)

---

| Document Detail | Information |
| :--- | :--- |
| **Project Name** | Muhideen |
| **Status** | Proposed — Rev 3 (marker taxonomy: Prayer Time vs Boundary Time Markers) |
| **Target Platforms** | Linux (Debian / Raspberry Pi OS), x86/ARM devices |
| **Primary Region Focus** | Malaysia (JAKIM Integration), extensible globally |
| **License** | MIT |

---

## 0. Design Reference (Non-Binding)

Expected, non-obligatory preview of one display theme. Final product does not need to pixel-match this mockup, but must meet readability and layout density shown (clock + next-prayer hero, 5 prayer cards with Adhan/Iqamah + a secondary Boundary Time Marker strip (Imsak/Syuruq/Dhuha), carousel hint, admin QR hint).

![Muhideen expected display mockup — non-obligatory reference](preview.jpg)

Notes on mockup vs requirements:
* Header: masjid name + zone label (e.g. `Gombak, Selangor / SGR01`), Gregorian + Hijri dates.
* Hero left: large real-time clock. Hero right: next prayer + time + Iqamah countdown.
* Cards: Fajr / Dhuhr / Asr / Maghrib / Isha — the 5 Prayer Time Markers, each with trilingual label; Imsak / Syuruq / Dhuha shown as Boundary Time Markers in a secondary position (no Adhan/Iqamah, never at card/hero level).
* Footer: carousel position indicator, admin QR hint (toggleable, auto-hidden during prayer states).
* Caption in mockup ("Screen will automatically dim...") is documentation only, not on-screen UI.

---

## 1. Executive Summary & Product Vision

### 1.1 Vision Statement
To create an accessible, lightweight, modern, and open-source digital signage system for mosques (*masjid*) and prayer halls (*surau*). The platform aims to bridge technical barriers for non-technical mosque administrators while providing a low-power, robust display system that functions seamlessly both online and offline.

### 1.2 Core Objectives
* **Zero Friction Management:** Allow mosque admins to configure settings via a simple local web interface without editing config files.
* **Resilient Dual-Mode Operation:** Seamlessly switch between online API sync (e.g., JAKIM E-Solat) and local cached schedules + calculation fallback.
* **Realistic Low Resource Footprint:** Backend stays ultra-light; full kiosk system requirements are stated honestly including Chromium (see §5.1, §7).
* **Distraction-Free Prayer Environment:** Automatically dim/blackout display during active prayer times to maintain solemnity.
* **Modular Multi-Display & Theming:** Support standalone or networked multi-screen setups with customizable and sandboxed community themes.

---

## 2. User Personas & Key Use Cases

```
                  +-----------------------------------+
                  |        MOSQUE COMMUNITY           |
                  +-----------------------------------+
                                    |
        +---------------------------+---------------------------+
        |                           |                           |
        v                           v                           v
+---------------+           +---------------+           +---------------+
|  1. Jama'ah   |           | 2. Mosque     |           | 3. System     |
|   (Public)    |           |    Admin      |           |    Integrator |
+---------------+           +---------------+           +---------------+
| Reads times,  |           | Configures    |           | Installs hardware,|
| countdowns,   |           | settings,     |           | creates custom    |
| announcements |           | uploads images|           | community themes  |
+---------------+           +---------------+           +---------------+
```

1. **Jama'ah (Public Visitor / Worshipper):** Needs clear, readable prayer/Iqamah times, count-downs, and announcements without visual clutter or distraction. Must be legible at 10m distance.
2. **Mosque Admin (Non-Technical Staff/Committee):** Needs an intuitive web dashboard accessible via phone or PC to adjust Iqamah buffers, upload posters, or change themes easily.
3. **System Integrator / Community Developer:** Technical volunteers who install the system, contribute new themes, or add integrations for international prayer calculation methods.

---

## 3. Functional Requirements

### 3.1 Prayer Time Engine (Core Service)

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-1.1** | **JAKIM API Sync** | Auto-fetch from JAKIM E-Solat (unofficial endpoint) by zone code (e.g. `SGR01`). Scheduled fetch daily at 02:00 local + exponential-backoff retry (5m, 15m, 1h). Cache 30 days forward + 7 days past for **configured zone only**. Monthly prefetch loop at install seeds ~365 days. Show `STALE` badge if cache age >48h. | High |
| **FR-1.2** | **Offline Mode + Fallback Chain** | Resolution order: (1) cached DB for `date+zone`, (2) on-device calculation (MABIMS defaults for MY), (3) last-known day + on-screen warning banner. System must run indefinitely offline once seeded. Only configured zone is preloaded; "all zones" bulk preload is out of scope. | High |
| **FR-1.3** | **International Calculation Engine** | Built-in calculation (MWL, ISNA, Egyptian, MABIMS/JAKIM params) by lat/lon + method + Asr juristic setting. Used as FR-1.2 fallback and primary mode outside MY. DST handled via IANA timezone, not fixed offset. | Medium |
| **FR-1.4** | **Iqamah Rule Management** | Per-Prayer-Time-Marker mode: `delay_minutes` after Adhan (defaults: Subuh 15, Dhuhr 10, Asr 10, Maghrib 10, Isha 15) OR `fixed_time`; Boundary Time Markers have no iqamah rule. Jumuah replaces Dhuhr on Friday with own rule. Stored in `iqamah_rules`. | High |
| **FR-1.5** | **Hijri Date + Offset** | Hijri calc (Umm-al-Qura / tabular, configurable) with manual offset -2..+2 days. Offset stored in `settings`, adjustable from Admin UI. | High |
| **FR-1.6** | **Time Sync Health** | Require NTP (`systemd-timesyncd`/`chrony`). Display `TIME UNSYNCED` warning if unsynced at boot or drift suspected. Optional DS3231 RTC documented for fully offline sites. Countdowns use monotonic clock. | High |
| **FR-1.7** | **Marker Taxonomy** | Two classes. Prayer Time Marker: Fajr, Dhuhr, Asr, Maghrib, Isha (Jumuah replaces Dhuhr Friday) — the only markers with Adhan, Iqamah, auto-dim, and state-machine transitions. Boundary Time Marker: Imsak, Syuruq, Dhuha — informational: no Adhan, no Iqamah, no auto-dim, never leaves `NORMAL`; optional countdown gated by `boundary_countdown` (default off) that never changes state; rendered below Prayer Time Marker level. JAKIM's 8 source markers map via the adapter to backend naming (§6.1). | High |

### 3.2 Display Client Interface (Public View)

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-2.1** | **Main Dashboard View** | Real-time clock (seconds optional), Gregorian + Hijri dates, 5 Prayer Time Markers at card/hero level + Imsak/Syuruq/Dhuha as secondary Boundary Time Markers, next-prayer highlight always a Prayer Time Marker, Iqamah countdown, opt-in boundary countdown. Trilingual labels: EN + BM + Arabic (Jawi optional). | High |
| **FR-2.2** | **Adhan Alert Overlay** | Full-screen state for configurable duration (default 3 min). Optional local audio: admin-uploaded chime/MP3 only — no bundled Adhan recitation (licensing/recitation variance). Volume schedule + mute respected. | High |
| **FR-2.3** | **Iqamah Countdown Mode** | Prominent live countdown after Adhan overlay ends until Iqamah time. Uses server-computed target + client monotonic tick with SSE resync. | High |
| **FR-2.4** | **Prayer Dimming / Blackout** | At Iqamah, enter `SALAH_DIM` for per-prayer duration (default 20 min, 45 min for Jumuah, configurable 5–60 min): dim to ≤10% brightness or black with minimalist clock only. Must be skippable by admin long-press. | High |
| **FR-2.5** | **Responsive / TV Scaling** | Fluid layout for 720p/1080p/4K, 16:9 primary, 9:16 vertical best-effort. Minimum 10m readability: hero clock ≥12vh, prayer times ≥3.5vh, WCAG AA contrast. No horizontal scroll. | High |
| **FR-2.6** | **Prayer State Machine** | Single source of truth, see §8. States: `NORMAL → PRE_ADHAN(-5m) → ADHAN → IQAMAH_COUNTDOWN → SALAH_DIM → NORMAL`. Jumuah overrides Dhuhr Friday. Boundary Time Markers never trigger PRE_ADHAN/ADHAN/IQAMAH_COUNTDOWN/SALAH_DIM: no Adhan, no Iqamah, no dim; their opt-in countdown never changes state. | High |

### 3.3 Information Carousel Module

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-3.1** | **Image Slideshow** | Cycle uploaded JPG/PNG/WebP (max 5MB each, max 50 items, auto-downscaled to 1920w). Supports Hadith/event/QR posters. Video out of scope for MVP. | Medium |
| **FR-3.2** | **Toggleable Module** | Global or per-group disable for prayer-time-only aesthetic. | High |
| **FR-3.3** | **Carousel Pause Rule** | Hidden from `PRE_ADHAN` (-5m) through end of `SALAH_DIM`. Footer indicator hidden likewise. | High |

### 3.4 Multi-Display & Grouping Management

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-4.1** | **Display Registration (pull model)** | No auto-scan. Displays are dumb Chromium clients loading `/display?id=<DISPLAY_ID>` and heartbeating `POST /api/displays/heartbeat` every 30s (updates `last_seen`, IP, group). Admin pre-registers or approves pending IDs. mDNS advertise `_muhideen._tcp` for discovery. | Medium |
| **FR-4.2** | **Display Grouping** | Groups (e.g. Main Hall, Lobby, Women's Section) with per-group theme + carousel + dim-duration override. | Low |
| **FR-4.3** | **Targeted Config + Realtime Push** | Global defaults + per-group override. Updates pushed via SSE (`/api/events`); clients poll every 60s as fallback. | Medium |

### 3.5 Theme & Customization Engine

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-5.1** | **Pre-installed Themes** | MVP ships **1** theme matching §0 mockup (`classic-green`). Second (`minimal-dark`) and third (`info-board`) deferred to Phase 2. | High (1 in MVP) |
| **FR-5.2** | **Sandboxed Community Themes** | Zip upload (max 2MB) with allowlist (`*.html,*.css,*.js,*.png,*.jpg,*.webp,*.woff2`), no symlinks/dotfiles/ executables, stripped on extract. Rendered in `<iframe sandbox="allow-scripts">` with CSP `default-src 'self'`, no access to `/admin` origin/APIs except read-only JSON via `postMessage`. Full JS allowed inside sandbox only. Manual approve step before publish. | High |
| **FR-5.3** | **Live Theme Preview** | Admin can preview any installed theme against live data in `/admin/preview?theme=` before publish. Publish is atomic per group. | Medium |

### 3.6 Admin Control Panel

| ID | Feature | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-6.1** | **Mobile-Friendly UI** | Responsive BM/EN UI, large touch targets, works offline over LAN. All dim/countdown durations editable without restart. | High |
| **FR-6.2** | **Setup Wizard + Minimal Settings API in MVP** | First-boot wizard: masjid name, zone/latlon+method, online/offline, admin password, hijri offset. Wizard is thin client over the same settings API used later — no wizard-only code path. | High |
| **FR-6.3** | **QR Fast-Connect (safe)** | QR encodes LAN URL only (`http://muhideen.local:8000/admin`), never credentials. First boot uses one-time setup token (expires after use/30min). QR hidden by default; shown on demand (admin keypress/button) and never during `ADHAN`/`SALAH_DIM`. mDNS + fallback IP shown alongside. AP-mode hotspot documented as fallback if LAN has client isolation. | High |
| **FR-6.4** | **Backup / Restore / Logs** | One-click export (`sqlite + media tar.gz`), import restore, `journald` log view + version display in Admin footer. | Medium |

---

## 4. Technical Architecture & Tech Stack

### 4.1 Evaluation (revised — backend RAM is non-decisive)

| Criteria | Option A: Node+Vue | Option B': Python sync+HTMX/Alpine (selected) | Option C: Go + Embedded FE (deferred) | Option D: PHP Laravel+SQLite |
| :--- | :--- | :--- | :--- | :--- |
| **Backend RAM idle** | ~120-200 MB | **~40-70 MB** | **~15-30 MB** | ~150-250 MB |
| **Build complexity** | High (npm/Vite) | **None at deploy (no Node, vendored wheels)** | Medium (Go toolchain) | Medium (Composer/Nginx) |
| **Community accessibility** | High | **Extremely High** | Moderate | High regionally |
| **Deploy artifact** | Node runtime | Python runtime + vendored wheel tarball | Single binary | PHP-FPM runtime |
| **Maintainability** | Moderate (dep drift) | **High (sync, SQLite-safe)** | High | Moderate |
| **Verdict** | Reject | **MVP** | **Phase 3 appliance** | Reject |

Backend-only RAM shown. Full kiosk system always adds 300–600MB for X11 + Chromium — this is OS/browser cost, not stack choice. Backend choice does not decide Pi viability; Chromium does.

* **A rejected:** npm/Vite build violates offline-first and no-build contributor goal; heaviest RAM + Chromium risks OOM on 2GB Pi.
* **D rejected:** Laravel + Nginx + PHP-FPM is heaviest ops burden for Pi with no advantage over B' for scheduler/SSE/image handling.
* **C deferred:** best long-term appliance (single binary, <100ms boot, trivial OTA), but prayer/Hijri lib maturity and volunteer familiarity are lower. Revisit once API contract (§4.3) is stable.

### 4.2 Selected Stack

**Option B' — Python 3.11+ (sync) + SQLite WAL + Jinja2 + HTMX / Alpine.js + hand-written vanilla CSS (ADR-0001).**

1. **Python native** on Debian/RPi OS Bookworm; stdlib `sqlite3`, APScheduler, Pillow, hostname/time handling. No Node at any stage. Toolchain is `uv` only (see CONTRIBUTING.md).
2. **Sync server (locked):** FastAPI with `def` sync endpoints + Uvicorn single worker. Pydantic DTOs are the executable contract (§6.3, `docs/api-contract.md`). No async DB code; SQLite accessed from a single writer thread. SSE via `text/event-stream`. OpenAPI docs at `/docs` (LAN + admin auth only).
3. **No build step, no Tailwind:** hand-written `static/app.css` (~10KB target). Tailwind Play CDN forbidden (offline + perf). Contributors editing HTML/themes never run Node; maintainers have no Node toolchain to keep.
4. **SQLite WAL behind ports (ADR-0003)** single-file, zero-admin. `PRAGMA journal_mode=WAL; synchronous=NORMAL;` + atomic backup via `VACUUM INTO`. Heartbeats received every 30s but flushed in 60s batches via single writer.
5. **Offline installer:** `uv.lock` + vendored wheel tarball (`vendor/wheels/`) built for `armv7l/aarch64/x86_64`. Install with `uv pip install --no-index --find-links vendor/wheels` or `uv sync --offline`. No live package index on device.

### 4.3 Architecture Rules (ports-and-adapters)

Domain core is framework-free so a later Go rewrite (Option C) needs no display/theme changes:

* `domain/`: `prayer_state` (§8 machine), `fallback_chain` (FR-1.2), `iqamah_calc`, `hijri` — no Flask/FastAPI/SQLite imports.
* Ports: `JAKIMClient`, `PrayerRepo`, `EventBus`, `MediaStore`.
* Adapters: `adapters/jakim_esolat.py`, `adapters/sqlite_repo.py`, `adapters/sse.py`, `adapters/cec.py`.
* Stable API contract: `GET /api/next-event`, `GET /api/events` (SSE), `POST /api/displays/heartbeat`, `GET /display?id=`. Use-case tests run with in-memory repos, no DB/network.

### 4.4 Development Constraints (API-first, backend-first, single repo)

Normative constraint to allow frontend-only and backend-only contributors to work without blocking each other:

1. **Single repo, single deployable.** No split repos or separately deployed frontend/backend services. Logical split only (§4.3, ARCHITECTURE.md).
2. **Ownership boundaries:**
   * Backend-owned: `src/muhideen/core/`, `domain/`, `engine/`, `adapters/`, `api/`, `migrations/`.
   * Frontend-owned: `src/muhideen/views/display/`, `views/admin/`, `static/app.css`, `static/app.js`, `themes/`, `api/fixtures/`.
   * Cross-boundary change = API contract change (§6.3, `docs/api-contract.md`). Frontend must never import `domain/`; backend must never embed presentation logic beyond Jinja context DTOs. Enforced by import-linter + purity scans (ARCHITECTURE.md).
3. **Backend-first, frontend-independent:** backend lands `api/` + OpenAPI + fixtures first. Frontend builds against fixtures/mock without a live DB or JAKIM access.
4. **Fixtures are normative:** `api/fixtures/next-event.json`, `api/fixtures/prayer-day.json`, `api/fixtures/events-stream.txt` (SSE sample) must be committed with every contract change. `uv run tools/mock_api.py` serves fixtures on `:8001` for frontend-only dev (stdlib only, no backend deps).
5. **No frontend toolchain:** frontend-only path requires browser + `uv run tools/mock_api.py` only. No Node/npm. CSS/JS hand-written; `themes/` validated by `uv run tools/lint_theme.py --theme <slug>`.

```
+-----------------------------------------------------------------------+
|                           MUHIDEEN STACK                             |
+-----------------------------------------------------------------------+
|  Frontend Layer    | HTML + static/app.css (vanilla, ~10KB) + Alpine.js + HTMX |
|  Realtime Sync     | SSE primary (/api/events), 60s poll fallback      |
|  App Server        | Python 3.11+ FastAPI-sync + Uvicorn 1 worker (ADR-0001) |
|  DB                | SQLite3 WAL (single file)                          |
|  OS & Runtime      | Debian / RPi OS + systemd + timesyncd/chrony       |
|  Client Display    | Chromium --kiosk (pull + heartbeat, §3.4)          |
+-----------------------------------------------------------------------+
```

---

## 5. Non-Functional Requirements (NFR)

### 5.1 Performance & Resource Limits (split)
* **Backend only:** ≤80 MB RSS idle, ≤150 MB during JAKIM sync; CPU <3% idle on Pi 3B+.
* **Full kiosk (backend+X11+Chromium):** ≤1 GB on Pi 4 2GB at 1080p idle; CPU <15% idle. This is the honest system budget.
* **Hardware tiers:** Recommended Pi 4 2GB+/Pi 5/x86 thin client for all-in-one. Pi 3B+ supported degraded (slower render). **Pi Zero 2 W (512MB) unsupported for all-in-one** — supported only as thin display client against a separate server, or headless server without local browser.
* **Boot:** backend `ready` ≤10s after `network-online.target`; first prayer render ≤30s on Pi 4, ≤45s on Pi 3B+ after OS boot (Chromium dominates). 15s all-in for kiosk is removed as unrealistic.

### 5.2 Reliability & Fault Tolerance
* Offline-first per FR-1.2 fallback chain; background sync never blocks display render.
* Stale-data banner if cache >48h or calc-fallback in use.
* Power-cut safety: WAL + `synchronous=NORMAL`, writes only via short transactions (sync job, admin save, heartbeat batch every 60s not every 30s write-through).

### 5.3 Readability / i18n / Audio
* WCAG AA contrast, 10m legibility test at 1080p, trilingual prayer names,RTL-safe Arabic.
* Audio opt-in, admin-uploaded only, per-prayer enable + master volume + quiet hours. No copyrighted Adhan bundled.

### 5.4 Security
* Admin auth: Argon2id (preferred) or bcrypt, rate-limit login (5/min/IP), expiring sessions, first-boot forced password change; one-time setup token.
* LAN-only by default; no inbound internet ports; `/docs` bound to LAN/admin auth.
* Uploads: images re-encoded via Pillow (strip EXIF), 5MB limit; theme zips validated per FR-5.2, extracted to `themes/<slug>/` with `manifest.json` required (`name,version,author,entry`).
* QR contains no secret (§3.6).

### 5.5 Developer & Contributor Experience (DX/CX)

Treated as first-class NFR for open-source multi-contributor work:

* **Frontend-only path:** browser + `uv run tools/mock_api.py` only, ≤5 min setup, no backend deps, no Node, no DB. Contract fixtures (§4.4) + `CONTRIBUTING.md` frontend track are sufficient to build display/admin/themes.
* **Backend-only path:** `uv sync --all-extras`, ≤15 min setup, `uv run pytest -m "unit or contract or integration"` runs domain use-cases with in-memory repos (no DB/network/Chromium).
* **No stepping on toes:** CI fails on (a) OpenAPI/fixture drift, (b) `domain/` importing web/DB, (c) `views/` importing `domain/`, (d) new Node/toolchain introduction.
* **Docs:** `CONTRIBUTING.md` maintains separate frontend-only and backend-only quickstarts; every API change ships fixtures + changelog entry.

---

## 6. Data Specifications & API Protocols

### 6.1 JAKIM Sync (unofficial — defensive client)
```http
GET https://www.e-solat.gov.my/index.php?r=esolatApi/takwimsolat&period=week&zone=SGR01
```
* Prefer `period=week/month` over `today` to reduce calls. Timeout 15s, UA pinned, 3 retries with backoff. Validate `HH:MM` + sane ordering (`Imsak < Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha`). Backend naming is canonical: the adapter maps source spellings (e.g. `subuh→fajr`, `zohor→dhuhr`, `isyak→isha`, `syuruk→syuruq`, `duha→dhuha`) to `MarkerName`; exact JSON keys + calc derivation for Imsak/Duha pinned by source research before 1A-6. Reject + keep cache on parse fail. Log zone + HTTP status. Document that endpoint may change without notice — admin can switch to calc-only mode.

### 6.2 SQLite Schema (v0.1)

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
  -- keys: masjid_name, zone_code|lat,lon,method, hijri_offset(-2..2),
  -- adhan_duration_s, dim_minutes_default, dim_minutes_jumuah,
  -- boundary_countdown(0|1), carousel_enabled, theme_default,
  -- qr_visible_default
);

CREATE TABLE prayer_times (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date_gregorian DATE NOT NULL,
  zone_code TEXT NOT NULL DEFAULT 'DEFAULT',
  imsak TEXT NOT NULL, fajr TEXT NOT NULL, syuruq TEXT NOT NULL,
  dhuha TEXT NOT NULL, dhuhr TEXT NOT NULL, asr TEXT NOT NULL,
  maghrib TEXT NOT NULL, isha TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'jakim', -- jakim|calc|manual
  fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(date_gregorian, zone_code)
);
CREATE INDEX idx_prayer_zone_date ON prayer_times(zone_code, date_gregorian);

CREATE TABLE iqamah_rules (
  prayer TEXT PRIMARY KEY, -- fajr,dhuhr,asr,maghrib,isha,jumuah (Boundary Time Markers excluded)
  mode TEXT NOT NULL DEFAULT 'delay', -- delay|fixed
  delay_minutes INTEGER DEFAULT 10,
  fixed_time TEXT -- HH:MM when mode=fixed
);

CREATE TABLE media (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  caption TEXT DEFAULT '',
  sort_order INTEGER DEFAULT 0,
  enabled INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE display_groups (
  name TEXT PRIMARY KEY,
  theme TEXT DEFAULT 'classic-green',
  carousel_enabled INTEGER DEFAULT 1,
  dim_minutes_override INTEGER -- NULL = use settings
);

CREATE TABLE displays (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  ip_address TEXT,
  group_name TEXT DEFAULT 'Default' REFERENCES display_groups(name),
  current_theme TEXT DEFAULT 'classic-green',
  last_seen DATETIME
);

CREATE TABLE users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL, -- argon2id
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Migrations: integer `PRAGMA user_version` + `alembic`-free hand-rolled `migrations/*.sql` applied at boot.

### 6.3 Frontend/Backend API Contract (normative, backend-first)

Single-repo logical split (§4.4). Backend implements first; frontend builds against fixtures.

* `GET /api/prayer-day?date=&zone=` → day `prayers` (5 Prayer Time Markers) + `boundaries` (3 Boundary Time Markers) + `source` + `stale` flag (see `api/fixtures/prayer-day.json`).
* `GET /api/next-event` → `{state, now, next_prayer, adhan_at, iqamah_at, dim_until, stale, next_boundary, boundary_at}` per §8 — `next_prayer` is always a Prayer Time Marker; `next_boundary`/`boundary_at` are populated only when `boundary_countdown` is enabled (see `api/fixtures/next-event.json`).
* `GET /api/events` → SSE `text/event-stream` (`state`, `tick`, `config-update`); 60s poll of `next-event` is fallback (see `api/fixtures/events-stream.txt`).
* `POST /api/displays/heartbeat` → `{id}` heartbeat; server batches `last_seen` writes every 60s.
* Breaking changes require major version bump (`/api/v2/...`) + fixtures + changelog; additive fields allowed without bump.

---

## 7. Deployment & OS Integration

### 7.1 Hardware Policy
All-in-one requires Pi 4 2GB+ / Pi 5 / x86. Zero 2 W only as thin client or headless server. Documented in installer preflight check (refuse all-in-one install on <1GB RAM with override flag).

### 7.2 Linux Setup
1. **Service:** `muhideen.service` (`After=network-online.target time-sync.target`, `Restart=always`), runs Uvicorn single worker on `127.0.0.1:8000` + LAN via `--host 0.0.0.0` behind admin auth. Single process, sync handlers, single SQLite writer.
2. **Kiosk:** `openbox` + Chromium:
   ```bash
   chromium --kiosk --noerrdialogs --disable-infobars --autoplay-policy=no-user-gesture-required \
     http://localhost:8000/display?id=HALL-01
   ```
   Display `?id=` is stable registration ID, not IP.
3. **Time:** enable `systemd-timesyncd` or `chrony`; optional DS3231 overlay documented.
4. **CEC (optional, Phase 2):** `cec-utils` ON 30m before Fajr, OFF 30m after Isha;Master toggle + per-group opt-out; never power-off during admin session.
5. **Installer (Phase 1):** `install.sh` does apt deps, `uv sync --offline` from vendored wheels, systemd unit, seed-fetch for configured zone, preflight RAM/disk check. OTA: `/api/version` + `update.sh` (git tag pull + `VACUUM INTO` backup first). Go single-binary noted as future Phase 3 appliance option, same API contract.

---

## 8. Prayer State Machine (normative)

```
NORMAL --(T-5m to Adhan)--> PRE_ADHAN [hide carousel+QR]
PRE_ADHAN --(Adhan time)--> ADHAN [fullscreen overlay, duration adhan_duration_s, default 180s]
ADHAN --(timeout)--> IQAMAH_COUNTDOWN [hero countdown to iqamah_rules target]
IQAMAH_COUNTDOWN --(Iqamah time)--> SALAH_DIM [dim/blackout, dim_minutes_default/jumuah]
SALAHT_DIM --(timeout or admin skip)--> NORMAL
```

Edge rules: Boundary Time Markers (Imsak, Syuruq, Dhuha) never trigger PRE_ADHAN/ADHAN/IQAMAH/SALAH_DIM — no Adhan, no Iqamah, no auto-dim; they render at secondary level and may show a countdown only when `boundary_countdown` is enabled, which never changes state. Jumuah replaces Dhuhr Friday (Khutbah time = Dhuhr Adhan). Midnight crossover: after Isha `SALAH_DIM`, next prayer is next-day Fajr. All transitions server-computed (`/api/next-event`) and SSE-pushed; client never hardcodes times.

---

## 9. Milestone Roadmap (revised)

```
  Phase 1: MVP (all-in-one)       Phase 2: Admin/Content        Phase 3: Multi-display polish
  +---------------------+         +---------------------+       +----------------------+
  | • Python sync+SSE+DB  |         | • 2 extra themes    |       | • Group overrides UI |
  | • JAKIM+cache+calc  |  --->   | • Carousel manager  |  ---> | • CEC power control  |
  | • 1 theme (§0)      |         | • Theme sandbox+zip |       | • Community library  |
  | • Settings API+wiz  |         | • Backup/restore    |       | • AP-mode + mDNS UX  |
  | • State machine+dim |         | • Audio upload      |       | • Go appliance eval  |
  | • install.sh+vendor |         |                     |       |                      |
  +---------------------+         +---------------------+       +----------------------+
```

* **Phase 1 (MVP):** 1a backend first (FastAPI-sync single worker, sync+fallback, schema §6.2, contract §6.3 + fixtures + mock-api, state machine §8, installer with vendored wheels + `uv.lock`); 1b frontend against fixtures (one vanilla-CSS theme matching preview, minimal settings API + wizard, dim). No group UI (single `Default` group), no CEC. Frontend-only contributors stay unblocked after 1a fixtures land.
* **Phase 2:** carousel manager, theme sandbox/upload/preview, backup/restore, audio upload, extra themes.
* **Phase 3:** grouping UI, targeted configs, CEC, community theme library docs, thin-client Zero 2 W image, Go single-binary appliance evaluation (drop-in behind §4.3 contract).

Out of scope MVP: video carousel, cloud dashboard, prayer-request messaging, zakat/khutbah CMS.
