---
name: muhideen-writing-plan
description: Use when you have a spec or slice for a multi-step Muhideen task, before touching code. Creates a right-sized plan in docs/development/plans/YYYY-MM-DD-<slug>.md. Triggers: write muhideen plan, plan this slice, plan this fix, plan this theme, implementation plan. Replaces generic writing-plans for this repo.
---

# Muhideen Writing Plan — Right-Sized

> **Why this exists:** generic planners produce ceremony-heavy plans (micro-steps, duplicated code blocks, cross-project paths). A capable Muhideen engineer needs slice-scoped plans that cite real paths, real fixtures, and real gates. Small stays small (80–250 lines); slice plans stay complete without bloat.

**Announce at start:** "I'm using the muhideen-writing-plan skill to create the implementation plan."

**Save plans to:** `docs/development/plans/YYYY-MM-DD-<slug>.md`. Never elsewhere unless overridden.

## Classify First — Don't Plan Trivial Work

| Tier | When | Files touched | Plan? | Typical size |
|---|---|---|---|---|
| **Trivial** | typo, copy fix, 1-line config | 1 | **No plan file** — inline todo, do it | 0 lines |
| **Small** | hotfix, single issue | 1–3 files + tests | Yes — lean | 80–300 lines, 2–3 tasks |
| **Medium** | engine/adapter change, 2–4 issues | 3–8 files | Yes — with Background | 350–1000 lines, 3–6 tasks |
| **Slice** | one `PHASES_AND_SLICES.md` slice (e.g. 1A-2, 1B-1, 2B) | per slice table | Yes — full slice shape | 800–1500 lines, 5–9 tasks |

Trivial → stop. Do not invoke `muhideen-momus-review` for trivial work.

## What Every Plan Keeps (non-negotiable)

1. **Header** — Goal / Architecture / Tech Stack / References (paths with lines) / Slice / Branch.
2. **File Structure** — exact `Create:` / `Modify:` / `Test:` paths. No "update relevant files".
3. **TDD per task** — failing test first, minimal fix, green. One QA block per task.
4. **Muhideen gates** — `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` (+ coverage 95) as final verify; contract parity (`tests/contract`) and `tools/lint_theme.py` named when touched.
5. **No placeholders** — never `TBD`, `TODO`, `appropriate handling`, `similar to Task N`.
6. **Type consistency** — DTO, fixture, and domain shapes match across tasks.
7. **Time discipline** — every time-dependent test pins `FakeClock`; no wall-clock reads outside `adapters/system_clock.py`.
8. **Contract discipline** — any cross-boundary change ships `docs/api-contract.md` + fixture + changelog entry in the same plan.

## What This Skill Drops (vs generic planners)

- 2–5-minute micro-steps → 2–4 meaningful bullets per task (failing test → implement → verify).
- `Interfaces: Consumes/Produces` per task → one-line `Depends on: Task N` except where a DTO shape flows task-to-task.
- Code block per step → one test name or reference snippet per task; link precedent under `src/muhideen/` instead of pasting bodies that drift.
- Commit-per-step → one commit per task, one `Expected: PASS` per verify.

## Plan Document Header

```markdown
# [Slice ID + Name] Implementation Plan (issue #NN)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** [One sentence — what resolves/renders, what issue closes]

**Architecture:** [Which layer changes — core/domain/engine/adapter/api/views — why, what is NOT changed]

**Tech Stack:** Python 3.11+, uv, FastAPI-sync + Uvicorn 1 worker, ruff, strict pyright, import-linter, pytest (unit/contract/integration/property/e2e).

**References:** Issue #NN, `docs/development/PHASES_AND_SLICES.md` slice row, `src/muhideen/...:L-L`, `docs/api-contract.md` §, `api/fixtures/...`, `docs/adr/*`

**Branch:** `feature/<slice-slug>` (from `dev`)

---
```

## File Structure

Lock decomposition before tasks:

```markdown
## File Structure

- Create: `src/muhideen/domain/prayer_state.py` — pure transition function
- Test: `tests/unit/test_prayer_state.py` — pinned-clock transitions
- Fixtures: `api/fixtures/next-event.json` — ADHAN countdown vector

No `src/muhideen/api` change (or: `src/muhideen/api/next_event.py` — DTO mapping).
```

For theme slices, start from the scaffolder: `uv run tools/new_theme.py <slug> --name "..."`, then fill. Never edit another theme to add a feature.

## Task Structure (all tiers)

```markdown
### Task 1: Failing guard — <what>

**Files:** `tests/unit/test_x.py`

- [ ] Add failing test(s): `test_<name>` covering <vectors>. Run: `uv run pytest tests/unit/test_x.py -k test_<name> -v` → Expected: FAIL.
- [ ] Implement minimal change in `src/muhideen/...`. Verify: same command → PASS; `uv run pytest -m unit -q` → PASS.
```

Contract tasks add: fixture update + `uv run pytest -m contract -q` → PASS. Theme tasks add: `uv run tools/lint_theme.py --theme <slug>` → ok.

## Self-Review (before saving, no subagent)

1. **Slice coverage:** every exit criterion in the slice row has a task.
2. **Placeholder scan:** `rg -n "TBD|TODO|appropriate|similar to Task" <plan>` → 0 hits.
3. **Shape consistency:** DTO ↔ fixture ↔ domain fields match across tasks.
4. **Momus dry-run:** would `muhideen-momus-review` say OKAY? References exist, tasks startable, QA has tool + steps + expected. Fix before handoff.

## Execution Handoff

```
Plan saved to docs/development/plans/YYYY-MM-DD-<slug>.md — N tasks, ~M lines.

Execute task-by-task via tdd (failing test first). After impl, run muhideen-momus-review on the plan file, then muhideen-oracle-review on the branch diff before PR handoff.
```

## References

- `docs/development/PHASES_AND_SLICES.md` — slice rows + entry/exit criteria
- `ARCHITECTURE.md` — layers, ownership, purity scans
- `TESTING_STRATEGY.md` — test layers and commands
- `docs/adr/*` — locked decisions
- Gates: `muhideen-momus-review` (plan), `muhideen-oracle-review` (post-impl), `tdd`, `verification-before-completion`
