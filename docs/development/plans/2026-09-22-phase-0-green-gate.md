# Phase 0 Green Gate Implementation Plan

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`.

**Goal:** Make the Phase 0 skeleton pass the full quality gate so Phase 1A slice work can start on green.

**Architecture:** No layer changes. Toolchain and hygiene only: lint/format fixes in `tools/` + `tests/conftest.py`, minimal skeleton tests that assert invariants already true (package imports, fixture files parse), lockfile + ignore + CI so the gate is enforced, not just runnable.

**Tech Stack:** Python 3.11+, uv, ruff (88 cols), strict pyright, import-linter, pytest (unit/contract markers only — deeper layers belong to 1A).

**References:** `docs/development/PHASES_AND_SLICES.md` Phase 0 table + exit gate, `CONTRIBUTING.md` gate command, `pyproject.toml` (ruff/pyright/import-linter/coverage config), `api/fixtures/next-event.json`, `api/fixtures/prayer-day.json`, `api/fixtures/events-stream.txt`, `tools/mock_api.py`, `tools/lint_theme.py`, `docs/adr/0001-backend-stack.md` (out of scope — no framework code here).

**Branch:** `feature/phase-0-green-gate` (from `main`)

**Baseline (evidence, 2026-09-22):** `ruff check` → 6× E501 (`tests/conftest.py:5`, `tools/lint_theme.py:1,34`, `tools/new_theme.py:34,39,62`); `ruff format --check` → 3 files reformat; `pytest -q` → exit 5, no tests collected; `pyright` → exit 0; `lint-imports` → KEPT (7 files, 0 deps). Missing: `uv.lock` (generated locally by `uv sync`, uncommitted), `tmp/` in `.gitignore`, `.github/workflows/ci.yml`.

---

## File Structure

- Modify: `tests/conftest.py` — wrap line 5 (E501)
- Modify: `tools/lint_theme.py` — wrap docstring line 1 and line 34 (E501 ×2)
- Modify: `tools/new_theme.py` — shorten embedded `THEME_JS` lines 34/39 and wrap line 62 (E501 ×3); `ruff format` applies to all three files
- Create: `tests/unit/test_package.py` — skeleton import test
- Create: `tests/contract/test_fixtures.py` — fixture parse/shape smoke test (no DTO impl — full parity is slice 1A-3)
- Create: `uv.lock` — commit generated lockfile (via `uv sync --all-extras`, already run locally)
- Modify: `.gitignore` — add `tmp/`
- Create: `.github/workflows/ci.yml` — full gate on push/PR (ruff check + format check + pyright + lint-imports + pytest)

No `src/muhideen/` changes. No `docs/api-contract.md` or fixture content changes.

---

### Task 1: Lint + format green

**Files:** `tests/conftest.py`, `tools/lint_theme.py`, `tools/new_theme.py`

**Goal:** Zero ruff errors, zero reformat diffs, behavior unchanged (fix line lengths by wrapping, not by rewording logic; keep generated theme output byte-identical — verify `new_theme.py` still scaffolds a `lint_theme.py`-clean theme).

- [ ] Wrap the 6 E501 lines. Verify: `uv run ruff check .` → PASS; `uv run ruff format .` then `uv run ruff format --check .` → PASS.
- [ ] Regression: `python3 tools/new_theme.py --help`, `python3 tools/lint_theme.py --theme classic-green` → ok; `git diff --stat themes/` → empty (scaffold output unchanged).

### Task 2: pytest collects green (skeleton only)

**Files:** `tests/unit/test_package.py` (create), `tests/contract/test_fixtures.py` (create)

**Goal:** `pytest -q` exits 0. Tests assert only what is already true; they are tripwires for accidental breakage, not slice 1A-3 parity.

- [ ] `test_package.py`: `import muhideen`, `muhideen.__file__` under `src/`, `py.typed` exists; markers `@pytest.mark.unit`.
- [ ] `test_fixtures.py`: `next-event.json` parses with keys `state/next_prayer/adhan_at/iqamah_at/dim_until/stale`; `prayer-day.json` parses with 6 `times` keys + `source`/`stale`; `events-stream.txt` non-empty with `event:`/`data:` lines; markers `@pytest.mark.contract`. No DTO imports (they don't exist yet).
- [ ] Verify: `uv run pytest -q` → PASS (exit 0, ≥2 tests); `uv run pytest -m "unit or contract" -q` → PASS.

### Task 3: Lock in toolchain (lockfile + ignore + CI)

**Files:** `uv.lock` (commit), `.gitignore` (add `tmp/`), `.github/workflows/ci.yml` (create)

**Goal:** Green is reproducible and enforced on every push/PR.

- [ ] Commit `uv.lock` from the local `uv sync --all-extras` run. Verify: fresh `uv sync --locked --all-extras` → exit 0.
- [ ] Append `tmp/` to `.gitignore`. Verify: `git check-ignore tmp/foo` → ignored.
- [ ] `ci.yml`: triggers push/PR, `uv sync --all-extras`, then the exact CONTRIBUTING gate (`ruff check`, `ruff format --check`, `pyright`, `lint-imports`, `pytest -q`) plus `lint_theme.py --theme classic-green`. Verify: YAML parses (`python3 -c yaml` or `gh workflow view` after push); full gate run locally → all green.
- [ ] Final verify (all green in one line): `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q`.

---

## Out of Scope (belongs to later slices)

- DTOs + full contract parity suite (1A-3), domain/engine/adapters code (1A-1/1A-2/1A-4+), views/templates/`static/app.css` (1B), installer/service (1A-8), CHANGELOG, version bump.
