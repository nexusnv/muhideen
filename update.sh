#!/usr/bin/env bash
# Muhideen OTA updater (PRD §7.2): refuse dirty trees, back up BEFORE the
# tag checkout, resync the venv, restart, health-verify — and fail loud
# with recovery info instead of auto-rolling back.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=packaging/lib.sh
source "$ROOT_DIR/packaging/lib.sh"

CHECK=0
MUHIDEEN_DRY_RUN=0
DB="/var/lib/muhideen/muhideen.db"
HEALTH_URL="http://127.0.0.1:8000/api/version"

usage() {
  cat <<'EOF'
usage: update.sh [--check] [--dry-run] [--db PATH]
  --check    print current tag, newest local tag and /api/version; mutate nothing
  --dry-run  print each mutating command instead of running it
  --db PATH  database to back up (default /var/lib/muhideen/muhideen.db)
EOF
}

while (( $# )); do
  case "$1" in
    --check) CHECK=1; shift ;;
    --dry-run) MUHIDEEN_DRY_RUN=1; shift ;;
    --db)
      (( $# >= 2 )) || die "--db requires a value"
      DB="$2"
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
backup_path="backups/${current}-${ts}.db"

maybe_run mkdir -p backups
maybe_run "$ROOT_DIR/.venv/bin/python" -c \
  'import sys; from pathlib import Path; from muhideen.adapters.sqlite_repo import Database, backup_to; backup_to(Database(sys.argv[1]), Path(sys.argv[2]))' \
  "$DB" "$backup_path"

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
