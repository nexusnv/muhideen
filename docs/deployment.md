# Deployment

Operating Muhideen on a device: prerequisites, first install, OTA updates,
time sync, optional RTC, and network discovery. Requirements are normative
in `PRD.md`; the API surface in `docs/api-contract.md`.

## Prerequisites

* **Hardware:** all-in-one tier = Raspberry Pi 4 2GB+ / Pi 5 / x86
  (PRD §7.1). Pi 3B+ runs the degraded tier; Pi Zero 2 W (512MB) is
  unsupported for all-in-one (thin client or headless server only).
* **OS:** Raspberry Pi OS or Debian with systemd; `git`, `curl`, and
  `uv` (<https://docs.astral.sh/uv/>), plus network for the first
  install's `apt` step.
* **Vendored wheels:** the device never touches a package index
  (PRD §4.2, item 5: offline installer). On a networked machine, from a checkout of the release
  tag, run `tools/build_vendor.sh` and ship the resulting `vendor/`
  directory (requirements + wheels) with the repo. It downloads the
  locked dependency set for the build host and cross wheels for
  `manylinux_2_28_aarch64` (Pi 4/5) and `manylinux_2_28_x86_64`;
  other architectures need a matching run of its `--platform` loop.
  32-bit `armv7l` offline installs are unsupported: four locked
  compiled dependencies (`argon2-cffi-bindings`, `cffi`, `markupsafe`,
  `pillow`) publish no `armv7l` wheels, and the vendor loop resolves
  `--only-binary`, so there is nothing to download. On a Pi 3B+ run
  64-bit Raspberry Pi OS (covered by the `aarch64` wheels) or use the
  thin-client/headless tiers.

## Install

```bash
sudo ./install.sh --zone SGR01 --masjid-name "Masjid Example"
```

| Flag | Effect |
| ---- | ------ |
| `--zone ZONE` | JAKIM zone recorded by `muhideen-seed` on first boot (e.g. `SGR01`). |
| `--masjid-name NAME` | Display name recorded on first boot. |
| `--hostname NAME` | System hostname to set; default `muhideen`; pass `''` to leave it unchanged. |
| `--force` | Continue past the RAM preflight refusal (see below). |
| `--dry-run` | Print every command without executing it (preflight reads still run). |

What each step does, in order:

1. **Preflight** — refuses to install when `MemTotal` < 1048576 kB
   (1GB RAM) and reports root free space; the refusal names the
   `--force` override (PRD §7.1 — the all-in-one tier needs Pi 4 2GB+).
   **`--force` warning:** overriding means the machine may not meet the
   backend + kiosk memory budget (PRD §5.1); use it only for headless
   servers or hardware you have measured yourself.
2. **apt dependencies** — `avahi-daemon`, `avahi-utils`, `git`, `curl`,
   plus `systemd-timesyncd` when available (best effort — a chrony host
   simply skips it).
3. **Offline sync** — refuses to continue if `uv` or `vendor/wheels` is
   missing (warns instead under `--dry-run`), then runs
   `uv sync --locked --offline --no-dev --find-links vendor/wheels` into
   `.venv` (`--no-dev`: the device never needs the dev tools the offline
   wheels don't carry).
4. **Service user** — creates the system user `muhideen` with home
   `/var/lib/muhideen`.
5. **Units** — copies `packaging/muhideen.service` and
   `packaging/muhideen-mdns.service` into `/etc/systemd/system`,
   baking this checkout's `.venv` path into `ExecStart`.
6. **Hostname** — `hostnamectl set-hostname` (default `muhideen`;
   skipped for `--hostname ''`).
7. **Seed** — runs `.venv/bin/muhideen-seed` against
   `--db /var/lib/muhideen/muhideen.db` with the configured zone and
   name (configure-or-sync: an already-configured database always
   re-fetches the configured zone's year and never reconfigures
   settings). If JAKIM is unreachable, seed
   warns and exits 3; the install continues, the services still enable,
   and the scheduler retries the fetch.
8. **Ownership + NTP** — `chown`s the state directory to `muhideen` and
   runs `timedatectl set-ntp true` (warn-only).
9. **Enable** — `systemctl daemon-reload` +
   `systemctl enable --now muhideen muhideen-mdns`.
10. **Health** — polls `http://127.0.0.1:8000/api/version` for up to
    10 s (20 × 0.5 s); on timeout it exits 1 and points you at
    `journalctl -u muhideen`.

The backend should be ready within 10 s of `network-online.target`
(PRD §5.1 budget). No step installs Python packages from the network.

## Update (OTA)

```bash
./update.sh --check      # read-only: what would happen
sudo ./update.sh         # apply the newest local tag
```

* **`--check`** prints the current git tag, the newest local tag, and
  the running `/api/version` — it fetches nothing and mutates nothing.
* A normal run, in order:
  1. refuses to continue if the working tree is dirty (commit or
     `git stash` first);
  2. backs up the database **before touching git** —
     `backups/<current-tag>-<timestamp>.db` via SQLite `VACUUM INTO`
     (`--db` selects the database, default
     `/var/lib/muhideen/muhideen.db`);
  3. `git fetch --tags` + `git checkout <newest tag>`;
  4. offline venv resync (same `uv sync` as the installer);
  5. `systemctl restart muhideen` (the mDNS unit follows — it is
     `PartOf=muhideen.service`);
  6. the same 10 s health budget as the installer.
* If git reports a "dubious ownership" error under `sudo`, mark the
  checkout trusted once:
  `git config --global --add safe.directory /path/to/checkout`.
* **Backups** live in `backups/` next to `update.sh` (anchor: the script
  `cd`s to its own directory, so this holds however you invoke it —
  override the location with `MUHIDEEN_BACKUP_DIR`, e.g. to keep them on
  the state volume). Names are timestamped and `VACUUM INTO` refuses to
  overwrite an existing file, so no backup is ever clobbered. Prune old
  ones manually.
* **Failure — no automatic rollback.** If the health check fails after
  an update, `update.sh` exits 1 with a recovery message naming the
  backup path, the previous tag, and the `systemctl` commands. Manual
  recovery:

  ```bash
  systemctl status muhideen; journalctl -u muhideen -n 100
  # revert the code:
  git checkout <previous tag>
  uv sync --locked --offline --no-dev --find-links vendor/wheels
  sudo systemctl restart muhideen
  # only if the database itself is suspect (stop the service first):
  sudo systemctl stop muhideen
  cp backups/<tag>-<timestamp>.db /var/lib/muhideen/muhideen.db
  sudo chown muhideen:muhideen /var/lib/muhideen/muhideen.db
  sudo systemctl start muhideen
  ```

## Time sync (NTP) and `TIME UNSYNCED`

FR-1.6: NTP is required (`systemd-timesyncd` or `chrony`);
`install.sh` enables it with `timedatectl set-ntp true`.

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

For sites with no network at all, a DS3231 RTC keeps wall time across
reboots (FR-1.6: optional, documented):

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

The installer sets up Avahi and enables `muhideen-mdns.service`, which
publishes the system hostname as a `_muhideen._tcp` service on port
8000:

* Default URL: `http://muhideen.local:8000` (admin:
  `http://muhideen.local:8000/admin`) — the QR fast-connect target
  (FR-6.3).
* **Fallback:** some networks block mDNS or isolate clients. Find the
  device IP (`hostname -I` or your router's lease list) and use
  `http://<ip>:8000` directly (FR-6.3 shows mDNS and the IP side by
  side).
* `--hostname ''` keeps the OS hostname, so the published name follows
  it.

## Service operations

```bash
systemctl status muhideen muhideen-mdns
journalctl -u muhideen -f
systemctl restart muhideen
```

Units live in `/etc/systemd/system/` (installed from `packaging/`);
`ExecStart` points at this checkout's `.venv`, so re-running
`install.sh` after moving the checkout re-bakes the path. The service
runs as the unprivileged `muhideen` user; state lives in
`/var/lib/muhideen/` (database + media).
