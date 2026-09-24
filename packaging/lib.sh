#!/usr/bin/env bash
# Shared helpers for install.sh and update.sh — the single source of truth
# for the two commands that mutate the environment (`sync_venv`,
# `health_check`) plus the dry-run-aware trace both scripts share, so the
# install and update flows can never drift apart on either.
#
# Sourced, not executed: the calling script sets MUHIDEEN_DRY_RUN first.

note() { printf '+ %s\n' "$1"; }

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

maybe_run() { # trace argv always; execute it only outside --dry-run
  if [[ "${MUHIDEEN_DRY_RUN:-0}" == "1" ]]; then
    printf '  dry-run: %s\n' "$*"
    return 0
  fi
  printf '  $ %s\n' "$*"
  "$@"
}

sync_venv() { # sync_venv <repo-root> — the only uv sync invocation (decision 9)
  local root="$1"
  note "venv: locked offline sync from vendor wheels"
  # --no-dev: the device never needs the dev group (pytest/ruff/pyright...),
  # and build_vendor.sh exports --no-dev — syncing without it would demand
  # dev wheels that offline vendor/ does not carry.
  (cd "$root" && maybe_run uv sync --locked --offline --no-dev --find-links vendor/wheels)
}

health_check() { # health_check <url> <retries> <interval-seconds>
  local url="$1" retries="$2" interval="$3" attempt=1
  note "health: curl -fsS ${url} (${retries} x ${interval}s budget)"
  if [[ "${MUHIDEEN_DRY_RUN:-0}" == "1" ]]; then
    printf '  dry-run: curl -fsS %s\n' "$url"
    return 0
  fi
  while (( attempt <= retries )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      note "health: ${url} ok on attempt ${attempt}"
      return 0
    fi
    if (( attempt < retries )); then
      sleep "$interval"
    fi
    attempt=$(( attempt + 1 ))
  done
  return 1
}
