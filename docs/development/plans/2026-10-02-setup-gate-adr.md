# Bless first-boot gate for setup (no token flow) — Implementation Plan (issue #40)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: self-review below (Momus unavailable this session), `oracle-review` on the branch diff before PR handoff.

**Goal:** Close issue #40 by amending the PRD to bless the shipped first-boot gate as the v1.0 setup mechanism and recording the decision in an ADR, so v1.0 no longer claims token security it does not have.

**Architecture:** Docs-only. No code, contract, fixture, or behavior change. PRD FR-6.3 + §5.4 security bullet reworded; new ADR-0004 captures context/decision/rationale/consequences including why a token flow was rejected for v1.0 and under what conditions it should be revisited.

**Tech Stack:** Markdown; repo gates (`ruff format` covers md code fences).

**References:** Issue #40 (decision recorded: amend PRD, keep gate); `PRD.md:126` (FR-6.3 token claim); `PRD.md:215` (§5.4 token claim); `docs/adr/0003-sqlite-behind-ports.md` (ADR format); `src/muhideen/api/app.py` setup route (open iff users table empty, else 409 — behavior being blessed; read to quote accurately); prior flags in `docs/development/plans/2026-09-26-1b-3-admin.md:11` and `2026-09-24-1a-7-http-surface.md:24` (locked interpretation, not shipped docs — do not touch).

**Branch:** `docs/setup-gate-adr` (cut when approved; base on `main`)

---

## Background the implementer needs

Locked decision (user, this session): no token endpoint in v1.0. The shipped mechanism — `POST /api/auth/setup` allowed only while the users table is empty (409 after), password-only, rate-limited setup endpoint, no default password ever existing — IS the one-time setup mechanism. Rationale for ADR: LAN-only threat model, single admin, no secret to transfer (a token would need display/transfer UX with its own phishing/shoulder-surfing surface), rate limits blunt racing. Revisit if: multi-admin, remote/WAN setup, or a security audit demands it.

## File Structure

- Modify: `PRD.md:126` — FR-6.3 cell: replace "First boot uses one-time setup token (expires after use/30min)." with first-boot-gate wording (open setup iff no admin exists; single-use via 409 after first admin; setup rate-limited; closes after first admin).
- Modify: `PRD.md:215` — security bullet: replace "; one-time setup token." with "; first-boot gate (setup open only before the first admin exists — see ADR-0004)."
- Create: `docs/adr/0004-first-boot-setup-gate.md` — Status Accepted, Date 2026-10-02, Context/Decision/Rationale/Consequences per 0003 format.
- Verify-only: `grep -rn -i "setup token" PRD.md ARCHITECTURE.md README.md docs/deployment.md docs/api-contract.md` → 0 hits after edit (session-cookie tokens in `auth.py` are real and unrelated — do not touch).

---

### Task 1: PRD amendments + ADR-0004

- [ ] Read `PRD.md:120-130` and `:210-220` for exact cell/bullet text; read `docs/adr/0003-sqlite-behind-ports.md` for format; read the setup route gating code to quote behavior accurately.
- [ ] Edit the two PRD lines (minimal wording, keep table/line structure). Write ADR-0004 (Context: FR-6.3 token ask vs shipped gate + prior flags; Decision: bless gate for v1.0; Rationale: LAN model/single-admin/no-transfer-secret/rate-limits; Consequences: no token endpoint, revisit triggers).
- [ ] Verify: `grep -rn -i "setup token\|one-time setup" PRD.md ARCHITECTURE.md README.md docs/deployment.md docs/api-contract.md` → 0 hits, and the only hit under `docs/adr/` is ADR-0004's own historical Context mention (intentional — it records the ask being decided against). Run: `uv run ruff format --check PRD.md docs/adr/0004-first-boot-setup-gate.md` → clean (ruff formats md code fences). Full `uv run pytest -q` → green (docs-only; guards against accidental edits).

## Self-review

1. Spec coverage: #40 either-branch → PRD-amendment branch fully covered (token-flow branch explicitly not taken per recorded decision); "v1.0 must not silently claim token security" verified by the zero-hits grep.
2. Placeholder scan: no TBD/TODO; exact lines named.
3. Type consistency: n/a (prose).
4. Momus dry gate: references verified this session (PRD lines + ADR format on disk); single task, startable, QA = grep + ruff + pytest commands with expected outputs.
