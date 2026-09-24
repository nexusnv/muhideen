#!/usr/bin/env bash
# Muhideen device installer: preflight → deps → venv → units → seed →
# NTP → enable → health (PRD §4.2.5 offline installer, §7.1 hardware
# policy). Every step traces its command to stdout; --dry-run prints each
# command and executes only the preflight reads.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=packaging/lib.sh
source "$ROOT_DIR/packaging/lib.sh"

ZONE=""
MASJID_NAME=""
NEW_HOSTNAME="muhideen"
FORCE=0
MUHIDEEN_DRY_RUN=0
MUHIDEEN_SYSTEMD_DIR="${MUHIDEEN_SYSTEMD_DIR:-/etc/systemd/system}"
STATE_DIR="/var/lib/muhideen"
HEALTH_URL="http://127.0.0.1:8000/api/version"

usage() {
  cat <<'EOF'
usage: install.sh [--zone ZONE] [--masjid-name NAME] [--hostname NAME]
                  [--force] [--dry-run]
  --zone ZONE        JAKIM zone passed to muhideen-seed (required first boot)
  --masjid-name NAME display name recorded on first boot
  --hostname NAME    system hostname to set ('' leaves it unchanged)
  --force            override the 1GB RAM preflight refusal (PRD §7.1)
  --dry-run          print each step's command; execute only preflight reads
EOF
}

while (( $# )); do
  case "$1" in
    --zone)
      (( $# >= 2 )) || die "--zone requires a value"
      ZONE="$2"
      shift 2
      ;;
    --masjid-name)
      (( $# >= 2 )) || die "--masjid-name requires a value"
      MASJID_NAME="$2"
      shift 2
      ;;
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

if [[ ! -d "$ROOT_DIR/vendor/wheels" ]]; then
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

seed_args=(--db "${STATE_DIR}/muhideen.db")
if [[ -n "$ZONE" ]]; then
  seed_args=(--zone "$ZONE" "${seed_args[@]}")
fi
if [[ -n "$MASJID_NAME" ]]; then
  seed_args+=(--masjid-name "$MASJID_NAME")
fi
maybe_run "$ROOT_DIR/.venv/bin/muhideen-seed" "${seed_args[@]}"

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
