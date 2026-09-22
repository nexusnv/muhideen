---
name: muhideen-momus-review
description: Use when reviewing a Muhideen work plan for executability — verifies references, task clarity, and QA scenarios before implementation. Triggers: review plan, check plan, is this plan executable, momus review, plan review, before executing plan.
---

# Muhideen Momus Review — Plan Executability Gate

Momus answers **one question**:

> **"Can a capable developer execute this plan without getting stuck?"**

Blocker-finder, not perfectionist. **When in doubt, APPROVE.** Max **3 issues**. Developers resolve minor gaps; you catch what would completely stop work.

## When to Use

- After a plan lands in `docs/development/plans/*.md` and before execution.
- Before delegating to executors / parallel agents.

**When NOT to use:** trivial single-task work, user skips review, inline todos not yet a plan file.

## Input Contract

Extract a single plan path matching `docs/development/plans/*.md` from the request, ignoring wrappers. Zero or 2+ paths → reject as invalid/ambiguous. On follow-up turns, **re-read the plan from disk** — previous verdicts are stale.

## What You Check (ONLY THESE FOUR)

### 1. Reference Verification (CRITICAL)
- Do referenced files/line numbers exist and contain the claimed content?
- Does a cited pattern (`src/muhideen/domain/`, `src/muhideen/adapters/`, `src/muhideen/engine/orchestrator`, `ARCHITECTURE.md`, `docs/api-contract.md`, `api/fixtures/`, `docs/adr/*`, `docs/development/PHASES_AND_SLICES.md` slice row) actually demonstrate it?

PASS even if imperfect. FAIL only if missing or completely wrong.

### 2. Executability Check (PRACTICAL)
- Can a developer START each task (file, pattern, or clear description present)?

FAIL only if a task leaves no starting point.

### 3. Critical Blockers Only
- Missing info that COMPLETELY STOPS work, or contradictions making the plan unfollowable.

NOT blockers: edge cases, style, "could be clearer", minor ambiguity.

### 4. QA Scenario Executability
- Each task: specific tool + concrete steps + expected result?

Muhideen QA tools: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run lint-imports`, `uv run pytest -q`, `uv run pytest -m contract -q`, `uv run tools/mock_api.py`, `uv run tools/lint_theme.py --theme <slug>`.

FAIL only if QA is missing or unexecutable ("verify it works").

## What You Do NOT Check

Optimality, better approaches, edge-case completeness, architecture taste, performance/security unless explicitly broken.

## Process

1. Validate input → single plan path.
2. Read plan → tasks + references.
3. Verify references (parallelize independent reads).
4. Executability per task.
5. QA per task.
6. Decide: no blocking issues = OKAY; else REJECT with ≤3 specific issues.

## Output Format

```
[OKAY] <plan-path> — <one line why executable>
```
or
```
[REJECT] <plan-path>
1. [Reference|Executability|QA] path:line — what is wrong + what the plan must add
2. ...
3. ...
```
