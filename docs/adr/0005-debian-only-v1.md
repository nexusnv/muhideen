# ADR-0005: Debian-Only v1.0 (Raspberry Pi Deferred)

Status: Accepted
Date: 2026-10-02

## Context
v1.0 readiness (issue #46) surfaced Pi-shaped holes: no Pi hardware to validate on, armv7l wheels unpublished for four locked dependencies, Pi-model tiers documented but never installed, and kiosk/CEC/watchdog behavior undefined. The product needs one shippable target, not three half-supported ones.

## Decision
v1.0 supports Debian Bookworm machines (x86_64/ARM64) + kiosk-mode Chromium only: any Debian box with 2GB+ RAM and a desktop UI for all-in-one; a TV browser or directly-connected display as the screen. Raspberry Pi support (any model) is explicitly deferred, not dropped.

## Rationale
* Testable surface: the team can install, measure, and sign off Debian on generic hardware today; Pi validation has no owner and no device.
* Wheel reality: x86_64/aarch64 manylinux wheels exist for every locked dep; armv7l does not — Debian ARM64 keeps the 64-bit path without a Pi-specific story.
* Kiosk honesty: Chromium kiosk + autoplay policy + Restart=always are definable on Debian now; Pi display/CEC/watchdog specifics stay unknown.

## Consequences
* PRD/deployment claim Debian-only; Pi model tiers, Pi OS, and armv7l install paths are out of v1.0 docs and support.
* `tools/build_vendor.sh` keeps the aarch64 cross loop (ARM64 Debian), armv7l stays unsupported.
* Revisit if: Pi hardware + a validation owner appear, or a deployment demands it — then a Pi tier returns as its own slice with install logs, not as a docs edit.
