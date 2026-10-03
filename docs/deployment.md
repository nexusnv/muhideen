# Deployment (v1.0: Linux + Docker + host-provisioned kiosk)

Operating Muhideen on a device: install the Dockerized app on generic
Linux, point admin-provisioned Chromium kiosks at the display URLs, and
operate updates, time sync, and backups. Requirements are normative
in `PRD.md`; the API surface in `docs/api-contract.md`. Raspberry Pi
bare-metal install and device registration are out of v1.0 scope
(issue #46).

## Prerequisites

* **Host:** any 64-bit Linux with Docker Engine and 2GB+ RAM for the
  all-in-one tier (backend + local kiosk browser, PRD 7.1).
  Below 1GB RAM: headless server only, with screens as thin browser
  clients on other machines.
* **Images:** `Dockerfile` + `compose.yml` at the repo root build the
  reproducible app image from the locked set (`uv.lock`, `--locked`:
  a drifted lockfile fails the build instead of shipping untested
  pins). No vendor-wheels bundle: offline sites ship the exported
  image tarball (`docker save`, see Install).
* **Clock:** the container inherits the host wall clock — run NTP on
  the host (`timedatectl set-ntp true`); the app only reports what the
  host gives it (see Time sync).

## Install

```bash
docker build -t muhideen:1.0.0 .
MUHIDEEN_VERSION=1.0.0 MUHIDEEN_PORT=8000 docker compose up -d
```

What this does, in order:

1. **Build** — resolves `uv.lock` (`--locked`) and installs the
   `muhideen` / `muhideen-seed` entrypoints into the image.
2. **Run** — starts one container (`restart: unless-stopped`) with
   port 8000 published and state in the `muhideen-data` volume
   (SQLite + uploads persist across image swaps — back up the volume,
   not the image). `TZ` defaults to `Asia/Kuala_Lumpur`.
3. **Seed** — first boot needs configuration: run the setup wizard at
   `/admin/setup` (or `muhideen-seed` against the volume-mounted DB
   for scripted installs). Without JAKIM reachability the scheduler
   retries; without coordinates the calc fallback stays off.
4. **Health** — the compose `healthcheck` polls `/api/version`;
   `docker compose ps` shows it, `docker compose logs muhideen` is the first
   stop on failure.

Offline install: on a networked machine
`docker save muhideen:1.0.0 > muhideen-1.0.0.tar`, carry the tarball
to the site, `docker load < muhideen-1.0.0.tar`, then
`docker compose up -d` as above.

## Offline-first: first boot needs connectivity or coordinates

The display never renders an empty or healthy-looking page without a
schedule. First boot must satisfy **one** of:

* **JAKIM reachability** — `muhideen-seed` (install step 3) fetches the
  configured zone's year into the prayer cache; when the fetch fails, the
  scheduler retries it (transient failures re-arm on a 6h long-pole —
  429 rate limits stay transient and retry; other, unrecoverable 4xx
  rejections never retry — recheck the zone code instead of waiting).
* **Coordinates** — `lat`/`lon` settings let the built-in MABIMS
  calculator resolve each day locally with no network at all.

Otherwise — fresh database, no coordinates, JAKIM unreachable — the
outcome is a documented slate, never a Clock: `GET /display` renders the
error slate, and schedule reads such as
`GET /api/next-event?now=…` answer 404 with a detail message (503 is
reserved for an installation with no settings at all). The SSE tick
likewise reports the `error` stage instead of `clock`, so the display
reloads into the route slate (see `docs/api-contract.md`).

* **Why tomorrow stays empty instead of backfilled:** the engine never
  sources tomorrow's Fajr from the last-known cache
  (`Engine._tomorrow` in `src/muhideen/engine/engine.py`) — a past
  template day must not seed tomorrow's times, because that would present
  wrong data as correct. Until the sync lands, tomorrow simply has no
  schedule; the display holds today's resolved state.

## Year boundary: December/January gap

* **Cause.** The JAKIM e-solat `period=year` endpoint serves the current
  calendar year only — rows stop at 31-Dec and the `date` parameter is
  ignored, so no fetch strategy can retrieve next-year rows from
  December. From early December the 30-days-forward cache window runs
  out of rows, and dates past 31-Dec resolve through the automatic
  fallback chain (calc / last-known) until the first January fetch.
* **Bridging (December).** Pin each needed January date by hand — in
  `/admin/settings` under Manual schedule (pick the date, check the 8
  times, Save pin), or directly: `PUT /api/manual-day` with a full
  8-marker `HH:MM` body plus `date` (times must be strictly increasing,
  else 422; a second PUT for the same date replaces the pin). The
  response echoes the pinned day with `"source": "manual"` and
  `"stale": true`, and the display carries the MANUAL banner. Pins
  outrank every automatic source (manual > JAKIM > calc) and the daily
  02:00 sync never overwrites them.
* **Auto-recovery (January).** The first successful daily sync in the
  new year fetches that year's full table, so unpinned January dates
  resolve automatically again — no action needed.
* **Releasing a pin.** Pins persist across syncs (the sync skips them),
  so hand a date back to the automatic schedule explicitly:
  `DELETE /api/manual-day?date=YYYY-MM-DD` (or Clear pin in the admin
  section; dates with no pin are 404). The date immediately falls back
  to the automatic chain, and the next sync re-saves the JAKIM row for
  it — no restart required.

## Update (new image)
A new release ships as a new image tag. Run these commands from the
source checkout for the release being deployed — the build packages
the current directory and does not fetch release source, so building
from a stale checkout tags old code with the new version.

```bash
docker build -t muhideen:1.0.1 .
MUHIDEEN_VERSION=1.0.1 docker compose up -d
```

1. Back up first: `POST /api/backup/export` from `/admin/settings`
   (the zip holds the DB snapshot + media — treat it as secret).
2. Build the new tag, then recreate the container against the same
   `muhideen-data` volume — the staged database migrates itself on
   boot; a backup from a newer app version than the running build is
   rejected, so never boot an older image over a migrated volume
   without restoring the matching backup first.
3. Confirm the release's application version via `/api/version`
   (the endpoint reports the app version from the release, not the
   Docker tag — compare against the release notes); on failure, roll
   back with
   `docker compose down && MUHIDEEN_VERSION=<prev> docker compose up -d`
   (plus `POST /api/backup/restore` if the database itself is suspect).
   **No automatic rollback:** the operator owns the backup-restore step.

## Backup and restore (one-click export)

The admin System section (`/admin/settings`) exports the whole installation
as one zip and restores it onto replacement hardware — no SSH or SQLite
needed. API: `POST /api/backup/export` (zip download),
`POST /api/backup/restore` (base64 zip), `GET /api/logs` (see
`docs/api-contract.md` for shapes and status codes).

**When to export.** Before every `update.sh` run (in addition to its
automatic pre-update DB backup), before replacing hardware, and after any
large media change (playlist images, adhan audio). There are no scheduled
or automatic exports — back up on demand, or add a cron job that POSTs the
export endpoint and stores the download off-device.

**What the bundle holds.** The `muhideen.db` snapshot at the zip root plus
the uploads tree under `media/` (playlist images, `adhan.mp3`). Caps are
defense-in-depth: total archive ≤256MB, per-member ≤64MB, member count
≤512. A restore payload travels as base64 JSON, so the practical request
ceiling is ~341MB of base64 (larger is 413); corrupt or traversal-unsafe
archives are rejected (400).

**Treat the archive as secret.** It contains the `users` password hashes —
handle it exactly like the live database file: encrypted transport, no
shared folders or chat uploads, delete working copies after the move.

**Restore onto replacement hardware.** Bring up the release compose
on the new device first, complete first-boot setup at `/admin/setup`
(a fresh volume has no admin yet — sign-in alone cannot proceed),
then sign in as admin and choose the backup file
in the System section and Restore (or POST the file base64 to
`/api/backup/restore`). The staged database is migrated before it replaces
the live one (older versions migrate up; backups from a newer application
version than the installed build are rejected), the media tree swaps atomically,
and no restart is required.

**What is NOT in the bundle.** Scheduler runtime state (in-memory retry
chains re-arm from the database on boot), admin sessions (in-memory — log
in again after a restore), and service logs (read live via the Logs panel
or `docker compose logs muhideen`; never stored in the archive).

## Time sync (NTP) and `TIME UNSYNCED`

FR-1.6: NTP is required (`systemd-timesyncd` or `chrony`) on the
Docker host (`timedatectl set-ntp true`); the container inherits the
host clock.

* **What the banner means.** The API reports `time_synced: false`
  (rendered as the `TIME UNSYNCED` banner) when either NTP reported
  unsynchronised at boot, or a wall-clock step larger than 5 seconds
  was detected against the monotonic clock. It is never silent.
* **What clears it.** A boot-time unsync clears as soon as NTP
  confirms synchronisation. A detected step additionally *latches* the
  warning for five minutes: only once the latch ages out **and** the
  probe confirms sync does `time_synced` return to `true` — a probe
  reading "still synced" cannot clear the latch early.
* **Checking by hand:**
  `timedatectl show -p NTPSynchronized` (or `chronyc tracking`), and
  `curl -sG http://127.0.0.1:8000/api/next-event \
  --data-urlencode "now=$(date -Iseconds)" | grep -o '"time_synced":[a-z]*'`
  (`now` is a required tz-aware parameter). The probe reads `timedatectl`
  first, falls back to `chronyc
  tracking`, and caches answers for 30 s.
* **Countdowns stay honest anyway:** state countdowns run on the
  monotonic clock, so a wrong wall clock shifts *which* prayer event
  is next, never the countdown arithmetic (FR-1.6).

### Optional DS3231 RTC (fully offline sites)

For sites with no network at all, an RTC keeps wall time across
reboots (FR-1.6: optional, documented). On generic Debian hardware,
use any kernel-supported RTC and manage it with `hwclock`
(`sudo hwclock -w` to seed, `sudo hwclock -s` to read at boot).
The GPIO/Debian-overlay wiring below targets Pi-class boards and is
deferred alongside Pi support (see ADR-0005):

1. Wire the module: `SDA`/`SCL` to the GPIO header, `3V3`, `GND`.
2. Enable the overlay — add to `/boot/firmware/config.txt` (older
   images: `/boot/config.txt`):

   ```ini
   dtoverlay=i2c-rtc,ds3231
   ```

3. Reboot, then seed the RTC from the correct system time:
   `sudo hwclock -w`. From then on the kernel reads the RTC at boot
   (`sudo hwclock -s` sets the system clock from it manually).
4. Honest expectation: with no NTP source, `time_synced` stays `false`
   and the `TIME UNSYNCED` banner remains visible — that is the probe
   telling the truth about NTP. Set the clock once (`date -s` or
   `hwclock -s`) and rely on the RTC plus the monotonic countdowns.

## Network: mDNS and the `.local` URL
Hostname publication is host provisioning (like the kiosk): if the LAN
needs `.local` names, run Avahi on the host and point it at the
compose-published port. The QR fast-connect target (FR-6.3) is whatever
LAN URL reaches the app (`http://<host>:8000/admin`); on networks that
block mDNS or isolate clients, use the host IP directly (`hostname -I`
or the router lease list).

## Service operations
```bash
docker compose ps
docker compose logs -f muhideen
docker compose restart muhideen
```

State lives in the `muhideen-data` volume (database + media); the image
itself is stateless. Port and tag come from `MUHIDEEN_PORT` /
`MUHIDEEN_VERSION`.

## Kiosk display
The repo ships no kiosk unit: the admin provisions Chromium on each
screen machine and points it at that screen's display URL. One install
serves many screens — `/display?id=hall` and `/display?id=entrance`
render independently, each with its own saved configuration (palette,
clock format, language, dim, carousel) from `/admin/settings`.

Validated kiosk invocation (Chromium/Chrome):

```bash
chromium --kiosk 'http://<host>:8000/display?id=hall' \
  --autoplay-policy=no-user-gesture-required
```

* `--kiosk` gives the full-screen display surface; the id selects the
  screen config (unknown ids render the global theme — configure the
  id first via `PATCH /api/displays/{id}` or the admin page).
* `--autoplay-policy=no-user-gesture-required` lets the adhan audio
  play without a click.
* The display reloads itself on state/stage changes over SSE, with a
  60s `next-event` poll fallback — a kiosk needs no watchdog beyond
  the browser's own restart-on-crash. On power loss the host reboots,
  compose restarts the backend (`unless-stopped`), and the provisioned
  kiosk reopens per host policy.
* A TV with a browser works as a thin display client pointed at the
  same URL shape — no software to install on it.
