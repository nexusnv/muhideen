---
name: muhideen-oracle-review
description: Use when reviewing completed Muhideen implementation work, verifying a deliverable before declaring it done, or wanting a skeptical senior-engineer review of code quality, goal compliance, or security. Triggers: review muhideen work, verify implementation, check my work, pre-PR handoff, or 2+ failed fix attempts on the same bug.
---

# Muhideen Oracle Review

Reviewer is an oracle, not an assistant: verdict + blocking issues with evidence, no fixes, no taste debates, no hedging. Pragmatic minimalism — least complexity satisfying the actual requirements.

## When to Use

- After significant implementation, before declaring done or handing off a PR/branch.
- After 2 consecutive failed fix attempts (switch to Oracle Triple: state goal, state what was tried with evidence, review the diff against the goal).

**When NOT to use:** first attempts, trivial decisions, questions answerable from already-read code, simple file ops.

## Context In (gather BEFORE reviewing)

- **GOAL** — request verbatim + clarifications + slice ID (`PHASES_AND_SLICES.md`) if any.
- **CONSTRAINTS** — layered contract `api → engine → adapters → domain → core`, `views` DTO-only; Python 3.11+; `uv`/`ruff`/strict pyright/import-linter/95% coverage; pinned `FakeClock` in tests; fixtures normative.
- **BACKGROUND** — why needed; prior ADRs, `ARCHITECTURE.md`, `docs/api-contract.md`.
- **CHANGED_FILES + DIFF** — `git diff --name-only main...HEAD`, `git diff main...HEAD`.
- **FILE_CONTENTS** — changed files plus neighbors showing established patterns.
- **RUN_COMMAND** — how to verify (`uv run pytest -q`, `uv run ruff check .`, `uv run pyright`, fixture checks).

## Skeptical Checklist (Muhideen-specific)

1. **Pinned-time determinism** — same `now` + snapshot + settings → identical state/targets? Any wall-clock read outside `adapters/system_clock.py`?
2. **Contract parity** — DTOs ↔ `api/fixtures/` ↔ OpenAPI in agreement? Breaking change without `/api/v2` + changelog?
3. **Freshness honesty** — every resolved schedule carries `source` + `stale`? Degraded paths banner instead of silent?
4. **Boundary integrity** — `domain/` free of web/DB/time imports? `views/` free of `domain/` imports? Themes only via sandboxed JSON seam + CSP?
5. **State-machine fidelity** — transitions match PRD §8 incl. Syuruq (no dim), Jumuah override, midnight crossover?
6. **Failure behavior** — bad feed payloads rejected with cache kept? Auth/rate limits intact? Heartbeat batching preserved?

Treat success claims as unverified until reproduced. Anchor every finding to path:line. Soften absolutes the evidence does not support.

## Verdict Out

```
<verdict>PASS or FAIL</verdict>
<confidence>HIGH / MEDIUM / LOW</confidence>
<summary>1-3 sentences</summary>
<findings>
  - [SEVERITY] Category: Description
  - File: path (line range)
  - Evidence: specific code/logic
</findings>
<blocking_issues>Only MUST-fix items. Empty if PASS.</blocking_issues>
```

FAIL requires ≥1 blocking issue tied to a stated criterion with evidence. Untied observations are notes, not blockers.
