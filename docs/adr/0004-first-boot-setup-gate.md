# ADR-0004: First-Boot Gate as the v1.0 Setup Mechanism (No Token Flow)

Status: Accepted
Date: 2026-10-02

## Context
PRD FR-6.3 asked for a one-time setup token (a secret that expires) for first-boot admin creation, which is safer than first-come-first-served on a shared LAN. What shipped instead is a first-boot gate: `POST /api/auth/setup` is allowed only while the users table is empty (409 once an admin exists), password-only with no default password ever existing, behind a per-IP setup rate limiter. Prior plan notes (1A-7, 1B-3) flagged the token as needing its own slice, and issue #40 required either landing a token flow or amending the PRD so v1.0 does not claim token security it lacks.

## Decision
Bless the first-boot gate as the v1.0 setup mechanism (PRD amended accordingly). No token issuance/validation endpoints in v1.0.

## Rationale
* LAN-only threat model with a single admin: there is no secret to transfer, so there is nothing to phish — a token would need display/transfer UX (console, screen, QR) whose shoulder-surfing surface exceeds the gate's.
* The gate is already single-use by construction (409 after the first admin, backed by the DB UNIQUE constraint that makes concurrent setups fail closed — the per-IP rate limiter only blunts repeat attempts); no default password means "forced password change" holds trivially.
* A token flow (issue/display/transfer, expiry, single-use, recovery when lost) is a new security surface disproportionate to v1.0's single-node appliance scope.

## Consequences
* No token endpoint, no setup-secret handling in installer/docs; first-boot security rests on reaching setup before an adversary on the LAN does (documented in the deployment guide's first-boot flow).
* Revisit if: multi-admin arrives, setup goes over WAN, or a security audit demands proof-of-possession at first boot.
