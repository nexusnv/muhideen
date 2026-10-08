#!/usr/bin/env bash
# Muhideen device installer: preflight → deps → venv → units → config →
# NTP → enable → health (PRD §4.2.5 offline installer, §7.1 hardware
# policy). Every step traces its command to stdout; --dry-run prints each
# command and executes only the preflight reads.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=packaging/lib.sh
source "$ROOT_DIR/packaging/lib.sh"

NEW_HOSTNAME="muhideen"
FORCE=0
MUHIDEEN_DRY_RUN=0
MUHIDEEN_SYSTEMD_DIR="${MUHIDEEN_SYSTEMD_DIR:-/etc/systemd/system}"
CONFIG_DIR="${MUHIDEEN_CONFIG_DIR:-/etc/muhideen}"
STATE_DIR="${MUHIDEEN_STATE_DIR:-/var/lib/muhideen}"
CONFIG_FILE="${CONFIG_DIR}/muhideen.json"
MEDIA_DIR="${STATE_DIR}/media"
VENDOR_WHEELS="${MUHIDEEN_VENDOR_DIR:-$ROOT_DIR/vendor/wheels}"
HEALTH_URL="http://127.0.0.1:8000/api/version"

usage() {
  cat <<'EOF'
usage: install.sh [--hostname NAME] [--force] [--dry-run]
  --hostname NAME    system hostname to set ('' leaves it unchanged)
  --force            override the 1GB RAM preflight refusal (PRD §7.1)
  --dry-run          print each step's command; execute only preflight reads

Configuration is a hand-edited JSON file: the installer copies
config/muhideen.example.json to /etc/muhideen/muhideen.json when missing
and never touches an existing one. Edit it for your masjid (name,
timezone, sync provider) — the service hot-reloads it in ~1s, no login.
EOF
}

while (( $# )); do
  case "$1" in
    --hostname)
      (( $# >= 2 )) || die "--hostname requires a value ('' to skip)"
      NEW_HOSTNAME="$2"
      shift 2
      ;;
    --force) FORCE=1; shift ;;
    --dry-run) MUHIDEEN_DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown flag: $1 (see --help)" ;;
  esac
done

preflight() {
  # PRD §7.1: all-in-one needs 1GB+; --force is the documented override.
  local meminfo="${MUHIDEEN_MEMINFO:-/proc/meminfo}"
  local mem_kb free_kb
  mem_kb="$(awk '/^MemTotal:/ {print $2; exit}' "$meminfo")"
  [[ -n "$mem_kb" ]] || die "preflight: cannot read MemTotal from ${meminfo}"
  if (( mem_kb < 1048576 )) && (( FORCE == 0 )); then
    die "preflight: MemTotal ${mem_kb} kB < 1048576 kB (1GB) — re-run with --force to override (PRD §7.1)"
  fi
  free_kb="$(df -Pk . | tail -n 1 | awk '{print $4}')"
  note "preflight: MemTotal ${mem_kb} kB, root free ${free_kb:-?} kB"
}

install_unit() { # install_unit <name> — @VENV@ placeholder → this checkout's venv
  local name="$1"
  note "unit: ${name} -> ${MUHIDEEN_SYSTEMD_DIR}"
  if [[ "$MUHIDEEN_DRY_RUN" == "1" ]]; then
    printf '  dry-run: sed @VENV@=%s/.venv  %s/packaging/%s > %s/%s\n' \
      "$ROOT_DIR" "$ROOT_DIR" "$name" "$MUHIDEEN_SYSTEMD_DIR" "$name"
    return 0
  fi
  sed "s|@VENV@|${ROOT_DIR}/.venv|g" "$ROOT_DIR/packaging/$name" \
    > "${MUHIDEEN_SYSTEMD_DIR}/${name}"
}

preflight

note "deps: avahi + curl + git via apt"
maybe_run apt-get install -y avahi-daemon avahi-utils git curl
maybe_run apt-get install -y systemd-timesyncd \
  || note "warn: systemd-timesyncd unavailable — skipped (best effort)"

if ! command -v uv >/dev/null 2>&1; then
  if [[ "$MUHIDEEN_DRY_RUN" == "1" ]]; then
    note "warn: uv not found — a real install needs it first"
  else
    die "uv not found — install uv from https://docs.astral.sh/uv/ and re-run (never curl|sh a remote script)"
  fi
fi

if [[ ! -d "$VENDOR_WHEELS" ]]; then
  if [[ "$MUHIDEEN_DRY_RUN" == "1" ]]; then
    note "warn: vendor/wheels missing — build it with ./tools/build_vendor.sh"
  else
    die "vendor/wheels missing — build it with ./tools/build_vendor.sh on a networked machine, then re-run"
  fi
fi

sync_venv "$ROOT_DIR"

maybe_run useradd --system --home-dir "$STATE_DIR" --create-home \
  --shell /usr/sbin/nologin muhideen \
  || note "useradd: muhideen already exists"
maybe_run mkdir -p "$MUHIDEEN_SYSTEMD_DIR"
install_unit muhideen.service
install_unit muhideen-mdns.service

if [[ -n "$NEW_HOSTNAME" ]]; then
  maybe_run hostnamectl set-hostname "$NEW_HOSTNAME"
else
  note "hostname: --hostname '' given, leaving the system hostname unchanged"
fi

# Don't rely on useradd's --create-home: when muhideen already exists that
# command is skipped, and the service would then fail writing its
# timetable cache into a missing dir.
maybe_run mkdir -p "$CONFIG_DIR" "$MEDIA_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
  # Re-installs must never clobber the admin's hand-edited config.
  note "config: ${CONFIG_FILE} already exists — leaving it (edit it for your masjid)"
else
  maybe_run cp "$ROOT_DIR/config/muhideen.example.json" "$CONFIG_FILE"
fi

ADMIN_TOKEN_FILE="${CONFIG_DIR}/admin_token"
if [[ -f "$ADMIN_TOKEN_FILE" ]]; then
  # Rotation is rewrite-the-file + restart; re-installs never rotate.
  note "admin_token: ${ADMIN_TOKEN_FILE} already exists — leaving it (rewrite + restart to rotate)"
elif [[ "$MUHIDEEN_DRY_RUN" == "1" ]]; then
  printf '  dry-run: python3 -c %s > %s\n' \
    "'import secrets; print(secrets.token_urlsafe(32))'" "$ADMIN_TOKEN_FILE"
  printf '  dry-run: chmod 0600 %s\n' "$ADMIN_TOKEN_FILE"
  printf '  dry-run: chown muhideen %s\n' "$ADMIN_TOKEN_FILE"
else
  # One-time generation: 256-bit token, owner-read-only, service-owned.
  note "admin_token: generating ${ADMIN_TOKEN_FILE} (one-time, never clobbered)"
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$ADMIN_TOKEN_FILE"
  chmod 0600 "$ADMIN_TOKEN_FILE"
  if ! maybe_run chown muhideen "$ADMIN_TOKEN_FILE"; then
    note "warn: chown ${ADMIN_TOKEN_FILE} failed — ensure User=muhideen can read it"
  fi
fi

# Pre-file-config installs kept all state in a SQLite database, which this
# layout no longer reads: flag it loudly so the operator recreates the
# settings by hand instead of wondering why the display is unconfigured.
# The check is a real file test (not maybe_run) so --dry-run stays silent
# unless a legacy database is actually present.
if [[ -f "${STATE_DIR}/muhideen.db" ]]; then
  note "warn: legacy database ${STATE_DIR}/muhideen.db is not used by file-config builds — recreate name/zone/manual-days/playlists in ${CONFIG_FILE} by hand (see docs/deployment.md 'Upgrading from a database install')"
fi

if ! maybe_run chown -R muhideen "$STATE_DIR"; then
  note "warn: chown ${STATE_DIR} failed — ensure User=muhideen can write it"
fi

if ! maybe_run timedatectl set-ntp true; then
  note "warn: timedatectl set-ntp failed — chrony-only host? TIME UNSYNCED shows if unsynced"
fi

maybe_run systemctl daemon-reload
maybe_run systemctl enable --now muhideen muhideen-mdns

if ! health_check "$HEALTH_URL" 20 0.5; then
  die "health check failed — see 'journalctl -u muhideen'"
fi

note "install complete: http://<host>.local:8000 (or the device IP)"
