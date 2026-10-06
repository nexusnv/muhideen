#!/usr/bin/env bash
# Muhideen OTA updater (PRD §7.2): refuse dirty trees, back up BEFORE the
# tag checkout, resync the venv, restart, health-verify — and fail loud
# with recovery info instead of auto-rolling back.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=packaging/lib.sh
source "$ROOT_DIR/packaging/lib.sh"

# Anchor every relative path (git ops, backups/) to this checkout, never the
# caller's cwd — admins run `sudo /path/to/update.sh` from anywhere.
cd "$ROOT_DIR"

CHECK=0
MUHIDEEN_DRY_RUN=0
CONFIG="/etc/muhideen/muhideen.json"
HEALTH_URL="http://127.0.0.1:8000/api/version"

usage() {
  cat <<'EOF'
usage: update.sh [--check] [--dry-run] [--config PATH]
  --check       print current tag, newest local tag and /api/version; mutate nothing
  --dry-run     print each mutating command instead of running it
  --config PATH config file to back up (default /etc/muhideen/muhideen.json)
EOF
}

while (( $# )); do
  case "$1" in
    --check) CHECK=1; shift ;;
    --dry-run) MUHIDEEN_DRY_RUN=1; shift ;;
    --config)
      (( $# >= 2 )) || die "--config requires a value"
      CONFIG="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown flag: $1 (see --help)" ;;
  esac
done

current_tag() { git describe --tags --abbrev=0 2>/dev/null || echo "none"; }
newest_tag() { git tag --sort=-version:refname | head -n 1; }

if (( CHECK )); then
  # Read-only: local tags + the running service, no fetch (mutates nothing).
  current="$(current_tag)"
  newest="$(newest_tag)"
  note "check: current tag ${current}, newest local tag ${newest:-none}"
  version="$(curl -fsS "$HEALTH_URL" 2>/dev/null || true)"
  note "check: GET ${HEALTH_URL} -> ${version:-unreachable}"
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  die "working tree dirty — commit your changes or 'git stash' them first (update refuses to move tags over uncommitted work)"
fi

current="$(current_tag)"
ts="$(date +%Y%m%dT%H%M%S)"
backup_root="${MUHIDEEN_BACKUP_DIR:-$ROOT_DIR/backups}"
backup_path="${backup_root}/${current}-${ts}.json"

# File-config state: snapshot the hand-edited config file before the tag
# checkout so a bad update can be recovered by copying it back.
# Scope note: only muhideen.json is snapshotted here. prayer_buffer.json
# is a regenerable sync cache (the scheduler refetches it), and media/
# (adhan audio, playlist images under /var/lib/muhideen/media) lives
# outside the repo and is never touched by the checkout — back up media
# separately if you need a full-media rollback.
# A missing config file is not fatal: DB-era boxes predate the JSON file
# entirely (see docs/deployment.md "Upgrading from a database install"),
# and there is simply nothing to snapshot yet. In --dry-run the cp is
# still traced so the order proof (backup before fetch/checkout) holds.
if [[ -f "$CONFIG" || "${MUHIDEEN_DRY_RUN:-0}" == "1" ]]; then
  maybe_run mkdir -p "$backup_root"
  maybe_run cp "$CONFIG" "$backup_path"
else
  note "config: $CONFIG missing — nothing to back up (fresh or pre-file-config install; continuing)"
fi

maybe_run git fetch --tags

newest="$(newest_tag)"
[[ -n "$newest" ]] || die "no tags found — fetch them first (git fetch --tags)"

maybe_run git checkout "$newest"

sync_venv "$ROOT_DIR"

maybe_run systemctl restart muhideen

if ! health_check "$HEALTH_URL" 20 0.5; then
  die "health check failed after update — no automatic rollback. Recovery: previous tag ${current} (git checkout ${current}), backup at ${backup_path}; inspect with 'systemctl status muhideen' and 'journalctl -u muhideen', then 'systemctl restart muhideen'"
fi

note "update complete: now at ${newest} (${HEALTH_URL})"
