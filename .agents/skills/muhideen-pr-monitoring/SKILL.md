---
name: muhideen-pr-monitoring
description: Triage review comments on a Muhideen PR (human or bot), verify CI, fix root causes on the PR branch, reply per thread, and raise grouped follow-up issues. Use when asked to monitor a PR, address review feedback, unblock a merge, or reconcile CI failures with review comments.
---

# Muhideen PR Monitoring

## When to Use

- User says `monitor PR #N`, `address review comments`, `unblock PR`, `triage CodeRabbit/human feedback`.
- A PR is green-but-blocked, red-checked, or review-heavy and needs systematic triage before fixes.

**Do NOT use for:** writing the original implementation, release planning (`muhideen-release-readiness`), or plan review (`muhideen-momus-review`).

## Protocol

### Step 1 — Read the PR
```sh
gh pr view <pr> --json number,title,headRefName,baseRefName,mergeable,mergeStateStatus,files
gh pr checks <pr>
```
Isolate work in a worktree on the PR head branch — never fix on `main`/`dev` directly.

### Step 2 — Collect every comment
List review threads (inline + general) with id, path:line, author, and body. Out-of-diff observations stay in scope — verify each against the code, don't dismiss for location.

### Step 3 — Triage each comment
| Verdict | Meaning |
|---------|---------|
| `fix-now` | Valid, in PR scope — schedule a fix |
| `deferred` | Valid, out of scope — needs a follow-up issue |
| `skipped` | Invalid (already fixed, not reproducible at path:line, contradicts `ARCHITECTURE.md`/ADR, taste-only) — reply only |

Triage before touching code. Fixing an untriaged queue launders vacuous suggestions into scope creep.

### Step 4 — Verify CI
Authoritative: `.github/workflows/ci.yml` when present, else the CONTRIBUTING gate:
```sh
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q
```
Plus when touched: `uv run pytest -m contract -q`, `uv run tools/lint_theme.py --theme <slug>`. A mergeable-but-BLOCKED PR with red checks is still blocked — fix the check, not the flag. Pre-existing failures unrelated to the PR get noted in the reply and triage table, never silently absorbed.

### Step 5 — Fix root causes on the PR branch
TDD where behavior changes. One commit preferred (`fix(<scope>): address PR #<n> review`), split only for logically independent fixes. Push to the PR head: `git push origin HEAD:<pr-head-branch>`.

### Step 6 — Reply + raise issues
- **Every** `deferred`/`skipped` comment gets a per-thread reply (`gh api repos/<owner>/<repo>/pulls/<pr>/comments/<id>/replies` for inline, `gh pr comment` for general).
- Group deferred items by root cause into follow-up issues (one issue per coherent scope, labeled for the next version), and link each issue back into its threads.
- Newfound bugs discovered during fixes: fix now only if clearly introduced by the PR and minimal; otherwise same deferred path.

### Step 7 — Final summary
Post one summary comment: triage table (id / verdict / reason / issue), CI before/after, gates run locally, commit pushed, issues created. Re-check `gh pr checks`.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Fixing before triage | Triage table first, always |
| Deleting a weak test instead of strengthening its assertion | Fix the assertion to its intended expectation |
| Replying only on the summary instead of the thread | Reply per thread for inline comments |
| One issue per comment | Group by implementation batch |
| Pushing to `dev`/`main` | Push to the PR head branch |
| Using unauthenticated web fetches for GitHub | Use `gh` CLI per `gh-cli` skill |
| Deferred items vanishing | Every deferred valid comment gets an issue |

## References

- `gh-cli` skill — authenticated GitHub ops
- `using-git-worktrees` skill — isolated PR worktrees
- `muhideen-oracle-review` — re-review after fixes
