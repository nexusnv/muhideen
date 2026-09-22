---
name: muhideen-release-readiness
description: Use when preparing Muhideen for a release — triggers include "prepare release", "release readiness", "cut a release", "draft release notes", "audit docs for drift", "pre-release check", or producing a readiness plan. Fires whenever a version bump or Pi image needs release-labelled issues triaged, the repo audited, the state matrix run, and a readiness plan emitted before anything ships.
---

# Muhideen Release Readiness Skill

> **Purpose:** turn "prepare for release" into a deterministic, evidence-backed protocol producing exactly three artifacts — triage + drift audit + state-matrix report, release notes, executable readiness plan — without touching production code. The plan executes afterward; this skill observes, measures, triages, plans.

## When to Use

- User says `prepare release 0.2.0`, `release readiness`, `ready to ship?`, `draft release notes`.
- A `PHASES_AND_SLICES.md` phase completes and a version/image needs stamping.

**Do NOT use for:** source research (`muhideen-source-research`), implementing tasks (TDD skills), post-merge reviews.

## Hard Rules (Non-Negotiable)

1. **Read-only on shipped code.** You may run the server, tests, and generators; you may NOT edit `src/`, `tests/`, `pyproject.toml`, or shipped docs. Fixes go into the plan as tasks. Permitted writes: `tmp/` scratch (gitignored), the three artifacts below, and triage-only GitHub label/comments (never delete/close without evidence comment).
2. **Evidence before claims.** Every drift finding cites file:line (or "absent from X"); every matrix row reproduces from the saved script with recorded seed; every issue verdict cites a reproduction command or ADR path.
3. **Three artifacts, exact locations:**
   - Triage + drift audit + matrix report → `docs/development/reports/YYYY-MM-DD-release-<version>-matrix.md`
   - Release notes draft → `docs/development/reports/YYYY-MM-DD-release-notes-<version>.md`
   - Readiness plan → `docs/development/plans/YYYY-MM-DD-release-<version>-readiness-plan.md`
4. **Respect `docs/development/AGENTS.md`.** Notes are ephemeral and self-contained; final release notes publish out-of-band (GitHub Release / CHANGELOG — decided in the plan).
5. **Ground in repo conventions:** `ARCHITECTURE.md`, `pyproject.toml`, `PRD.md` first. `uv run` for everything. `gh-cli` skill for GitHub ops. Semver governs the number.
6. **Determinism is a test:** the matrix runs twice and requires byte-identical outputs (scoped to clock + snapshot).
7. **Escalate judgment calls** (`needs-decision` in the plan, raised to the user): behavior-change mismatches, `wontfix` contradicting an ADR, CHANGELOG adoption.

## Phase 0 — Discovery (10 minutes)

1. Resolve the version (`pyproject.toml` vs target; ask only if they differ).
2. Classify the delta (`git log --oneline <last-tag>..HEAD` → semver; mismatches are findings).
3. Record repo state: branch, SHA, date (UTC), Python, `uv lock --check`.
4. Inventory the release surface: `README.md`, `ARCHITECTURE.md`, `CONTEXT.md`, `CONTRIBUTING.md`, `TESTING_STRATEGY.md`, `LICENSE`, `PRD.md`, `docs/adr/`, `docs/api-contract.md`, `api/fixtures/`, `themes/*/manifest.json`, `pyproject.toml`, `src/muhideen/py.typed`, CLI/service files, `install.sh`/`update.sh` when present.
5. Inventory release-labelled issues (`gh label list` to confirm convention, then list open + closed-since-tag).

## Phase 1 — Issue Triage

Every release-labelled open issue ends as `invalid` / `wontfix` / `duplicate` / `done` (with evidence comment + close) or confirmed-open → finding `I1…In` → P0 plan tasks. Verify implementation status by code search, never by assumption. Contradictory `wontfix` → `needs-decision`. Record the full table in the report.

## Phase 2 — Drift Audit

| # | Check | Method |
|---|-------|--------|
| 1 | Fixtures vs DTOs vs OpenAPI | contract tests + manual diff of `api/fixtures/` against handlers |
| 2 | `CONTEXT.md` terms vs code vocabulary | one entry per domain term; no implementation leaks |
| 3 | `ARCHITECTURE.md` vs imports | import-linter passes; no new cross-boundary imports |
| 4 | Docstring/API drift | sample public handlers; stale version strings grepped across tree |
| 5 | Forbidden references | no shipped file references `docs/development/` |
| 6 | ADR statuses | Accepted/Superseded consistent with merged code |
| 7 | Broken links + hardcoded counts | all relative links resolve; counts derived, never hardcoded |
| 8 | Packaging | `pyproject.toml` metadata, `py.typed` present, LICENSE year |
| 9 | Theme manifests | every `themes/*/manifest.json` valid; entry files exist; `lint_theme.py` passes |
| 10 | Installer/service | `install.sh`, `muhideen.service` match current ports/paths |

Output: drift table (`D1…Dn`, severity P0/P1/P2, evidence, proposed fix).

## Phase 3 — State-Matrix Experiment (release-scoped)

Scope to slices changed since `<last-tag>` (`git diff --stat <last-tag>..HEAD -- src/`): `domain/`/`engine/` changes ⇒ full matrix; otherwise affected prayers/zones only. Record the derivation — full scope on slice-local diffs is a scope bug.

1. Scratch: `tmp/release-matrix/<YYYY-MM-DD>-<version>/` (`tmp/` gitignored; adding it is the only permitted ignore edit, recorded as a plan task if missing).
2. Generator (`generate_matrix.py`, saved): ≥100 rows across the in-scope surface, ≥6 per prayer/state (nominal, variant, invalid feed, stale, absent, multi-boundary), fixed recorded seed, with `expect_state/expect_source/expect_stale` oracle columns.
3. Runner (`run_matrix.py`): seeded DB + pinned clocks through the ASGI app (`httpx` test transport or live test server via `uv run`), full JSON dump per row.
4. Run twice; byte-identical required.
5. Analyze: mismatch matrix (finding per row → `E1…En`, behavior-change candidates → `needs-decision`), distribution sanity (0 INVALID rows = too-soft corpus, regenerate), freshness completeness (every degraded row banners), spot-check countdown values not just states.
6. Report in the matrix file: § Triage, § Drift, § Experiment, § Gates.

## Phase 4 — Release Notes Draft

From `git log <last-tag>..HEAD` + triaged issues + audit/matrix evidence: Summary (3 sentences), Highlights, Added/Changed/Fixed/Deprecated/Removed/Security, Breaking changes & migration, Quality dashboard (tests, coverage, gates), Install & upgrade (Pi image + `uv sync --offline` path), Known limitations.

## Phase 5 — Gates (run, record, plan on failure)

| # | Gate | Command / check |
|---|------|-----------------|
| G1 | Full quality gate | `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest` |
| G2 | Coverage floor | `uv run pytest --cov --fail-under=95` |
| G3 | Fixture parity | `uv run pytest -m contract -q` |
| G4 | Theme lint | `uv run tools/lint_theme.py --theme <each>` |
| G5 | Build + service smoke | build artifact installs; `muhideen.service` starts; backend ready ≤10s; `/api/version` responds |
| G6 | Kiosk render checklist | Chromium kiosk loads `/display`, all 5 states reachable via pinned clocks, dim path verified |
| G7 | Dependency hygiene | `uv lock --check`; stale/vulnerable pins noted |

## Phase 6 — Readiness Plan

Merge findings (`I*`/`D*`/`E*`/`G*`, `needs-decision`) into the dated readiness plan: header (title, date, status Draft, branch `release/<version>`, sources, target), findings→task table, progress table, ordered tasks (P0 correctness → P1 drift → P2 notes/CHANGELOG/version → P3 tag/image checklist; TDD per behavior fix), Decisions section, ship checklist (merge → tag `v<version>` → image → verify → GitHub Release).

## What Not to Do

- Fix during audit — findings only.
- Matrix on all prayers when only slice-local files changed (and vice versa: `domain/`/`engine/` changes always mean full matrix).
- All-nominal corpus without degraded rows.
- Reference `docs/development/` from shipped files.
- Close/relabel issues without evidence comments.
- Save artifacts anywhere but the three exact paths.

## Red Flags — STOP and Re-plan

- "Docs look fine" without command evidence.
- Matrix run once; seed unrecorded; scope underived.
- Untriage release-labelled issue remains.
- Source edited during readiness (revert into a plan task).
- `needs-decision` silently resolved.
