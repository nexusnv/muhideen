# Contributing to Muhideen

Thanks for your interest. Read `ARCHITECTURE.md` and `TESTING_STRATEGY.md` before writing code. `CONTEXT.md` is the glossary — use its terms.

## Development Environment

Requires Python 3.11+. All commands via `uv` — no Makefile, tox, or bare pip.

```bash
git clone <repository-url>
cd muhideen
uv sync --all-extras
```

Frontend-only contributors skip the install: see track 1 below.

## Two Independent Tracks

### Track 1 — Frontend-only (no backend deps)

```bash
uv run tools/mock_api.py        # serves api/fixtures/ on :8001 (stdlib only)
# open views/ templates or themes/ entry against http://localhost:8001
uv run tools/lint_theme.py --theme classic-green
```

Build display/admin/themes against `docs/api-contract.md` + `api/fixtures/`. Never import `domain/`. Hand-written CSS/JS only — no Node/npm. Every contract need becomes a fixture request, not a backend import.

### Track 2 — Backend-only (no browser needed)

```bash
uv sync --all-extras
uv run pytest -m "unit or contract or integration"
```

Domain work uses `FakeClock` + in-memory repos; integration tests run against a tmp-file SQLite database (WAL) created per test. No DB server, no network, no Chromium required.

## Quality Gate (must pass before PR)

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run tools/lint_theme.py --theme classic-green
! rg -n "fastapi|httpx|datetime\.now|time\.time" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/
! rg -n "sqlite3" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/ tests/unit/
uv run pytest
```

Rules: strict pyright on `src/`, no `# type: ignore` / `# noqa` in `src/`, frozen domain dataclasses, test doubles local to their file, TDD (failing test first).

## Branching and Release

* `main` is releasable. Feature branches are cut from `main` and merged back to `main` via PR.
* Release: tag `vX.Y.Z` on `main` after the release scope merges, then build the Pi artifact. Non-release chores ride the next release.

## Pull Request Process

1. Branch from `main`.
2. Write the failing test first where applicable. Contract changes ship fixtures + `docs/api-contract.md` update + changelog entry in the same PR.
3. Run the full gate above.
4. Open PR against `main`, describe what/why, reference issues.
5. New themes use the scaffolder: `uv run tools/new_theme.py <slug> --name "..."` then fill in. Never edit another theme to add a feature — scaffold or extend the shared seam.
