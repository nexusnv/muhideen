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
   `muhideen` entrypoint into the image.
2. **Run** — starts one container (`restart: unless-stopped`) with
   port 8000 published and `./config` + `./media` bind-mounted
   (the JSON config, the timetable cache, and media persist across
   image swaps — back up the files, not the image).
   `TZ` defaults to `Asia/Kuala_Lumpur`.
3. **Configure** — first boot needs `config/muhideen.json`: copy the
   example into place, then edit name/timezone plus the schedule
   provider block for your masjid:

   ```bash
   cp config/muhideen.example.json config/muhideen.json
   # edit config/muhideen.json, then:
   sudo chown -R $(id -u):$(id -g) config media
   ```

   The service fails fast when the file is missing and hot-reloads
   hand-edits live (~1s) — no login, no rebuild. The `chown` matters:
   the image runs as the unprivileged `muhideen` user, so without it
   the sync worker cannot write `prayer_buffer.json` (the boot log
   warns and the timetable never caches). Without JAKIM reachability
   the scheduler retries; without coordinates the calc fallback stays
   off.
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

* **JAKIM reachability** — the scheduler fetches the configured
  zone's year into the prayer cache on its daily 02:00 run (no fetch at
  boot — a fresh install serves calc/manual fallback until the first
  successful sync); when the fetch fails, the scheduler retries it
  (transient failures re-arm on a 6h long-pole —
  429 rate limits stay transient and retry; other, unrecoverable 4xx
  rejections never retry — recheck the zone code instead of waiting).

## Schedule providers (JAKIM or Aladhan)

`schedule.sync_provider` selects the sync source — it has **no default**,
so every install chooses explicitly (`"jakim"`, `"aladhan"`, or `"none"`).
`"none"` is explicit offline: no fetch ever runs (calc/manual only),
for fully offline sites. The zone
code is a provider argument, not profile identity: `masjid` holds only
`name` + `timezone`, while `schedule.jakim.zone` carries codes like
`SWK08`. `schedule.zone` is an optional served-zone label (API param,
display, buffer key): unset falls back to the JAKIM fetch key, else
`"local"`.

```json
"sync_provider": "jakim",
"jakim": { "zone": "SWK08" },
"aladhan": {
  "base_url": "https://aladhan.api.islamic.network/v1",
  "method": 17
},
```

* `jakim.zone` — e-solat zone code, required for `"jakim"`. Fetched rows
  are stamped with the served-zone label, so the label may differ from
  the upstream code (e.g. label `"surau-alhuda"` fetching `"SWK08"`).
* `aladhan.base_url` — `api.aladhan.com` and
  `aladhan.api.islamic.network` are verified live mirrors of the same
  core; any compatible host works (there is no `.ru` mirror — that host
  does not resolve, so don't use it). Any path suffix is trimmed.
* `aladhan.method` — Aladhan calculation-method id (`17` = JAKIM, the
  default, keeps Malaysian numbers closest to e-solat).
* School follows the existing `asr_juristic` (`shafi`→0, `hanafi`→1);
  Dhuha derives as Sunrise + `dhuha_offset_min` (Aladhan serves no Dhuha
  marker). `lat`/`lon` are required (rejected at load without them).
* Synced rows carry `ALADHAN` provenance: fresh, so no badges render
  (the display shows provenance only as tiny `offline` / `calculated` /
  `manual` legend pills when degraded; `calc_only` installs chose that
  mode explicitly and render none).
* Provider, host, and method are boot config: changing them needs a
  service restart (the sync client is built once, like the timezone).
  Zone label, coordinates, and offsets hot-reload as usual.
* **Fully offline** — `"sync_provider": "none"` (or the legacy
  `calc_only: true` switch): the scheduler never fetches, the display
  renders no source badges, and times resolve from coordinates via the
  on-device calculator, or from hand-entered `manual_days` pins when no
  coordinates are set. Coordinates are recommended but not required.
* **Coordinates** — `lat`/`lon` settings let the built-in MABIMS
  calculator resolve each day locally with no network at all.
* **Calculation is the mandatory fallback** — every resolve ends at the
  on-device calculator (`method` defaults to `MABIMS`, `asr_juristic` to
  `shafi`) whenever the higher sources miss a marker: the full
  per-marker precedence is manual pin → provider row → calc. Calc runs
  whenever coordinates are set; without them the chain ends at the
  last-known row or an honest empty slate.

Otherwise — empty config, no coordinates, JAKIM unreachable — the
outcome is a documented slate, never a Clock: `GET /display` renders the
error slate, and schedule reads such as
`GET /api/next-event?now=…` answer 404 with a detail message (503 is
reserved for a missing or invalid config file). The SSE tick
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
* **Bridging (December).** Pin each needed January date by hand: add
  an entry to `schedule.manual_days` in `config/muhideen.json` (or
  `/etc/muhideen/muhideen.json` on systemd installs) with the date plus
  any subset of the 8 `HH:MM` markers (at least one) — the merged day
  must stay strictly increasing, duplicate dates are rejected. Present
  pin markers override everything; missing markers fall through
  per-marker (pin → provider → calc), so a one-marker correction needs
  no full retype. The service hot-reloads the file (~1s, validated
  before swap; a bad edit keeps the last-good pins serving).
  The response surface echoes the pinned day with `"source": "manual"`
  and `"stale": true`, and the display carries the `manual` pill. Pins
  outrank every automatic source (manual > provider > calc) and the daily
  02:00 sync never overwrites them. A partial pin with no synced row to
  complete against fails loudly until the row syncs.
* **Auto-recovery (January).** The first successful daily sync in the
  new year fetches that year's full table, so unpinned January dates
  resolve automatically again — no action needed.
* **Releasing a pin.** Pins persist across syncs (the sync skips them),
  so hand a date back to the automatic schedule explicitly: delete the
  entry from `manual_days` and save — the date immediately falls back
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

1. Back up first: copy `config/muhideen.json` aside (and `media/`
   separately if you changed adhan audio or playlist images — the
   updater snapshots only the JSON file; see Backup and restore).
2. Build the new tag, then recreate the container against the same
   bind mounts — there is no database to migrate. `prayer_buffer.json`
   is a regenerable cache the scheduler refetches, so it needs no
   backup; a config file written by a newer app version than the
   running build may fail validation until you update the image.
3. Confirm the release's application version via `/api/version`
   (the endpoint reports the app version from the release, not the
   Docker tag — compare against the release notes); on failure, roll
   back with
   `docker compose down && MUHIDEEN_VERSION=<prev> docker compose up -d`
   (plus copy your config backup back if the file itself is suspect).
   **No automatic rollback:** the operator owns the backup step.

## Upgrading from a database install

File-config builds cannot read the old SQLite database: there is no
DB→JSON importer. `install.sh` warns when it sees
`/var/lib/muhideen/muhideen.db`, and `update.sh` skips its snapshot
when no JSON config exists yet — neither migrates anything.

To carry an install forward by hand:

1. Read the old settings (zone, masjid name, timezone, manual pins,
   playlists, theme knobs) from the running v1.0 surface or the admin
   pages before updating.
2. Update, then write them into `config/muhideen.json` (copy
   `config/muhideen.example.json` first) and drop adhan audio /
   playlist images into `media/`.
3. `docker compose up -d` and check `/display` plus
   `/api/prayer-day?date=…&zone=…` resolve.

## Backup and restore (file copies)

There is no admin UI and no export API: the installation is two JSON
files plus a media tree, so backup is copying them.

* **What to copy.** The hand-edited config (`config/muhideen.json` on
  compose, `/etc/muhideen/muhideen.json` on systemd) and, when media
  changed, the media tree (`media/` on compose,
  `/var/lib/muhideen/media` on systemd). `prayer_buffer.json` is a
  regenerable sync cache — copy it if you like, or let the scheduler
  refetch it.
* **When.** Before every image update (the Compose flow creates no
  automatic backup), before replacing hardware, and after any large
  media change. There are no scheduled exports — back up on demand,
  or add a cron job that copies the files off-device.
* **Restore onto replacement hardware.** Install fresh, copy the config
  (and media) into place with the same ownership the service user can
  read, and start — the watcher validates on load and serves 503
  slates until the file parses.
* **Treat old archives as secret.** Pre-file-config backup zips contain
  the `users` password hashes — handle them like the live database
  file and delete working copies after the move. Current file backups
  hold no credentials.

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
compose-published port. The kiosk target (FR-6.3) is whatever
LAN URL reaches a display (`http://<host>:8000/display?id=hall`); on
networks that block mDNS or isolate clients, use the host IP directly
(`hostname -I` or the router lease list).

## Service operations
```bash
docker compose ps
docker compose logs -f muhideen
docker compose restart muhideen
```

State lives in the bind-mounted files (config + timetable cache +
media); the image itself is stateless. Port and tag come from
`MUHIDEEN_PORT` / `MUHIDEEN_VERSION`. Adhan audio dropped into `media/`
is served at `/media/adhan.mp3`; playlist `image_path` files are
addressable under `/media/` (template carousel rendering is a future
slice). `/docs`, `/redoc`, and `/openapi.json` are public by decision —
they expose only the read-only public schemas; the contract tests pin
their parity.

## Kiosk display
The repo ships no kiosk unit: the admin provisions Chromium on each
screen machine and points it at that screen's display URL. One install
serves many screens — `/display?id=hall` and `/display?id=entrance`
render independently, each with its own presentation entry (language,
theme overlay, dim, carousel flag) from the `displays` map in
`config/muhideen.json`.

Validated kiosk invocation (Chromium/Chrome):

```bash
chromium --kiosk 'http://<host>:8000/display?id=hall' \
  --autoplay-policy=no-user-gesture-required
```

* `--kiosk` gives the full-screen display surface; the id selects the
  screen config (unknown ids render the global theme — add the id to
  the `displays` map in the JSON config).
* `--autoplay-policy=no-user-gesture-required` lets the adhan audio
  play without a click.
* The display reloads itself on state/stage changes over SSE, with a
  60s `next-event` poll fallback — a kiosk needs no watchdog beyond
  the browser's own restart-on-crash. On power loss the host reboots,
  compose restarts the backend (`unless-stopped`), and the provisioned
  kiosk reopens per host policy.
* A TV with a browser works as a thin display client pointed at the
  same URL shape — no software to install on it.
