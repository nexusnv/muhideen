#!/usr/bin/env bash
# Maintainer-side vendor builder (needs network; never runs on device, CI,
# or an offline site — decision 8). Emits vendor/requirements.txt +
# vendor/wheels for install.sh's offline `uv sync`, covering the locked
# deps for the build host plus cross wheels for the device arches.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=packaging/lib.sh
source "$ROOT_DIR/packaging/lib.sh"

# Anchor vendor/ + uv build outputs to the checkout, not the caller's cwd.
cd "$ROOT_DIR"

MUHIDEEN_DRY_RUN=0
REQS="vendor/requirements.txt"
WHEELS="vendor/wheels"

usage() {
  cat <<'EOF'
usage: tools/build_vendor.sh [--dry-run]
  --dry-run  print each command instead of running it (no network used)
EOF
}

while (( $# )); do
  case "$1" in
    --dry-run) MUHIDEEN_DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown flag: $1 (see --help)" ;;
  esac
done

maybe_run mkdir -p "$WHEELS"

note "export: locked runtime requirements (no hashes, no project)"
maybe_run uv export --no-dev --no-hashes --no-emit-project -o "$REQS"

note "download: locked wheels for the build host"
maybe_run python -m pip download -r "$REQS" -d "$WHEELS"

note "download: hatchling (the build backend, for offline uv sync)"
maybe_run python -m pip download hatchling -d "$WHEELS"

# Cross wheels for the devices: Pi OS Bookworm ships Python 3.11, and
# requires-python is >=3.11 — cp311 covers aarch64 (Pi 4/5) and x86_64
# (dev VMs). Other tiers are tracked outside this script.
for plat in manylinux_2_28_aarch64 manylinux_2_28_x86_64; do
  note "download: cross wheels ${plat}"
  maybe_run python -m pip download -r "$REQS" -d "$WHEELS" \
    --python-version 3.11 --abi cp311 --only-binary=:all: --platform "$plat"
  maybe_run python -m pip download hatchling -d "$WHEELS" \
    --python-version 3.11 --abi cp311 --only-binary=:all: --platform "$plat"
done

note "build: muhideen project wheel"
maybe_run uv build

note "vendor: ready in ${WHEELS} (commit or ship the directory with the repo)"
