# Timezone hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the two timezone one-liners (#56 guard, #57 import) with pins, superseding PR #83.

**Architecture:** Two independent single-line prod edits, each with a colocated test pin. Either order works; commit separately.

**Tech Stack:** Python 3.12, pytest (unit mark), pydantic v2 file models.

---

### Task 1: `TypeError` guard + `None` pin (#56)

**Files:**
- Modify: `src/muhideen/core/values.py:406`
- Modify: `tests/unit/test_core_values.py:655` (`test_timezone_defaults_to_kl_and_rejects_unknown`)
- Test: `tests/unit/test_core_values.py::test_timezone_defaults_to_kl_and_rejects_unknown`

- [ ] **Step 1: Extend the test with the `None` case**

```python
    with pytest.raises(ValueError, match="unknown timezone"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            **{"timezone": None},
        )
```

Append inside `test_timezone_defaults_to_kl_and_rejects_unknown` after the
`Mars/Olympus` block. The `**{"timezone": None}` form keeps strict type
checkers quiet (no `type: ignore`, which this repo bans in `src/`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_core_values.py::test_timezone_defaults_to_kl_and_rejects_unknown -v`
Expected: FAIL — `TypeError` escapes instead of `ValueError` (proves the gap; the test asserts `pytest.raises(ValueError)`)

- [ ] **Step 3: Add `TypeError` to the guard**

In `src/muhideen/core/values.py:406`, change:

```python
        except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
```

to:

```python
        except (ValueError, TypeError, ZoneInfoNotFoundError, KeyError) as exc:
```

Nothing else in the file changes.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_core_values.py::test_timezone_defaults_to_kl_and_rejects_unknown -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/core/values.py tests/unit/test_core_values.py
git commit -m "fix(settings): catch TypeError in timezone guard (#56)"
```

### Task 2: `TIMEZONE_DEFAULT` import + default pin (#57)

**Files:**
- Modify: `src/muhideen/adapters/file_models.py:19` (import list), `:95` (default)
- Modify: `tests/test_file_config_schema.py` (append pin; check file tail for placement/style first)
- Test: new `test_masjid_timezone_defaults_to_single_owner` (or colocated equivalent if the file has a defaults test — extend that instead, no duplicate)

- [ ] **Step 1: Add the pin test**

```python
def test_masjid_timezone_defaults_to_single_owner() -> None:
    from muhideen.adapters.file_models import Masjid
    from muhideen.core.values import TIMEZONE_DEFAULT

    assert Masjid(name="M").timezone == TIMEZONE_DEFAULT
```

(If `Masjid` requires more fields, construct the minimal valid instance the file's existing tests use — read the file first, match its style. If a defaults test already exists, extend it instead of adding a new function.)

- [ ] **Step 2: Run to verify it passes on current code**

Run: `uv run pytest tests/test_file_config_schema.py -q -k timezone`
Expected: PASS even before the prod edit (both spell the same string today) — this pin guards against future drift, not current divergence.

- [ ] **Step 3: Make the one-line prod edits**

In `src/muhideen/adapters/file_models.py`, add `TIMEZONE_DEFAULT` to the
existing `from muhideen.core.values import (...)` block (alphabetical spot:
after `ThemePalette`, before the closing paren — read the list first), and
change line 95 to:

```python
    timezone: str = TIMEZONE_DEFAULT
```

- [ ] **Step 4: Run the schema suite**

Run: `uv run pytest tests/test_file_config_schema.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/adapters/file_models.py tests/test_file_config_schema.py
git commit -m "refactor(config): Masjid timezone default via TIMEZONE_DEFAULT (#57)"
```

### Task 3: Full gate, PR, supersede #83

- [ ] **Step 1: Run the full gate**

Run: `uv run pytest -q`
Expected: all green

Run: `uv run ruff format --check . && uv run ruff check .`
Expected: clean (run `uv run ruff format` on touched files first if needed, as a separate `style:` commit)

- [ ] **Step 2: Final review of the diff**

Run: `git log --oneline main..HEAD && git diff main --stat`
Expected: only the two prod one-liners, two test pins, and spec/plan docs

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin fix/timezone-hardening-56-57
```

PR title: `fix(timezone): guard None + single-owner default (#56, #57)`. Body: two one-liners, gate evidence, `Closes #56, #57. Supersedes #83 (same guard change; #57 folded in).`

- [ ] **Step 4: Close PR #83 as superseded**

Comment on #83 crediting it (same fix, folded into the new PR with #57 added), then close without merge.
