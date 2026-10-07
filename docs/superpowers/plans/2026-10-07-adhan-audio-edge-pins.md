# Adhan-audio edge pins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin the decided-keep-current semantics of #58 and #60 with unit tests, extracting one pure predicate so the `None` case is testable. (#59 dropped 2026-10-07: `_is_mp3` was deleted by PR #94 — nothing left to pin; issue closed as obsolete.)

**Architecture:** One verbatim-move extraction (`_adhan_playback_allowed` next to `_in_quiet_hours` in `api/app.py`; route calls it with identical arguments) plus two additive tests in the existing `tests/unit/test_adhan_audio.py`. No behavior change anywhere.

**Tech Stack:** Python 3.12, pytest (unit mark), stdlib only.

---

## File structure

- Modify: `src/muhideen/api/app.py:18` — extend the `collections.abc` import with `Sequence`.
- Modify: `src/muhideen/api/app.py:164-174` — add `_adhan_playback_allowed` after `_in_quiet_hours` (pure, keyword-only).
- Modify: `src/muhideen/api/app.py:822-831` — route's `if (...)` becomes a call to `_adhan_playback_allowed(...)`; the `resolve_adhan_path`/`is_file` try-block beneath it is untouched.
- Modify: `tests/unit/test_adhan_audio.py` — append three tests (function-level imports, matching file style). No other test files change.

---

### Task 1: Pin quiet-hours `start == end` (#58)

No prod change: `_in_quiet_hours` already returns `False` for the empty range. A pin test passes on current code; the run step verifies the pin holds (no red phase exists when pinning current behavior).

**Files:**
- Modify: `tests/unit/test_adhan_audio.py` (append at end)
- Test: `tests/unit/test_adhan_audio.py::test_quiet_hours_start_eq_end_never_quiet`

- [ ] **Step 1: Append the pin test**

```python
def test_quiet_hours_start_eq_end_never_quiet() -> None:  # Issue #58
    from muhideen.api.app import _in_quiet_hours

    assert _in_quiet_hours("12:00", "12:00", "12:00") is False
    assert _in_quiet_hours("00:00", "00:00", "00:00") is False
    # Sanity rows: established ranges keep behaving.
    assert _in_quiet_hours("23:00", "22:00", "06:00") is True
    assert _in_quiet_hours("07:00", "22:00", "06:00") is False
    assert _in_quiet_hours("12:00", "09:00", "17:00") is True
    assert _in_quiet_hours("08:59", "09:00", "17:00") is False
    assert _in_quiet_hours("12:00", None, None) is False
```

- [ ] **Step 2: Run the test to verify the pin holds**

Run: `uv run pytest tests/unit/test_adhan_audio.py::test_quiet_hours_start_eq_end_never_quiet -v`
Expected: PASS (pin of current behavior; a FAIL here means the behavior drifted and the GitHub decision must be revisited, not the test)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_adhan_audio.py
git commit -m "test(audio): pin quiet-hours start==end as never quiet (#58)"
```

### Task 2: DROPPED — MP3 sniff vectors (#59)

Struck 2026-10-07: `_is_mp3` does not exist on `main` (deleted by PR #94,
which replaced byte-sniff upload validation with media-relative path
validation). No test to write; issue #59 closed as obsolete. No commit.

### Task 3: Extract `_adhan_playback_allowed` and pin the `None` path (#60)

This is the only prod touch: a verbatim move of the route's pure
condition into a testable predicate. TDD red phase is real here (the
name does not exist yet).

**Files:**
- Modify: `src/muhideen/api/app.py:18`
- Modify: `src/muhideen/api/app.py:164-174` (insert after `_in_quiet_hours`)
- Modify: `src/muhideen/api/app.py:822-831` (route calls the predicate)
- Modify: `tests/unit/test_adhan_audio.py` (append at end)
- Test: `tests/unit/test_adhan_audio.py::test_adhan_playback_allows_none_next_prayer`

- [ ] **Step 1: Write the failing test**

```python
def test_adhan_playback_allows_none_next_prayer() -> None:  # Issue #60
    from muhideen.api.app import _adhan_playback_allowed

    assert (
        _adhan_playback_allowed(
            enabled=True,
            next_prayer=None,
            muted_prayers=["fajr"],
            now_hhmm="12:00",
            quiet_start=None,
            quiet_end=None,
        )
        is True
    )


def test_adhan_playback_respects_muted_quiet_and_enabled() -> None:  # Issue #60
    from muhideen.api.app import _adhan_playback_allowed

    allowed = dict(
        enabled=True,
        next_prayer="fajr",
        muted_prayers=[],
        now_hhmm="12:00",
        quiet_start=None,
        quiet_end=None,
    )
    assert _adhan_playback_allowed(**allowed) is True
    assert _adhan_playback_allowed(**{**allowed, "muted_prayers": ["fajr"]}) is False
    assert (
        _adhan_playback_allowed(
            **{**allowed, "quiet_start": "09:00", "quiet_end": "17:00"}
        )
        is False
    )
    assert _adhan_playback_allowed(**{**allowed, "enabled": False}) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_adhan_audio.py::test_adhan_playback_allows_none_next_prayer -v`
Expected: FAIL with `ImportError` (`_adhan_playback_allowed` does not exist yet)

- [ ] **Step 3: Add the predicate and extend the import**

In `src/muhideen/api/app.py`, change line 18 to:

```python
from collections.abc import AsyncGenerator, Sequence
```

Insert after `_in_quiet_hours` (after line 174):

```python
def _adhan_playback_allowed(
    *,
    enabled: bool,
    next_prayer: str | None,
    muted_prayers: Sequence[str],
    now_hhmm: str,
    quiet_start: str | None,
    quiet_end: str | None,
) -> bool:
    """Pure eligibility leg of the display adhan-audio rule (#58, #60).

    ``None`` next prayer falls through as not-muted (safe default: the
    overlay only renders during ADHAN, which always has a next prayer).
    ``start == end`` quiet bounds match nothing (half-open ``[start, end)``).
    File resolution/existence stays in the route (I/O + 503 mapping).
    """
    return (
        enabled
        and next_prayer not in muted_prayers
        and not _in_quiet_hours(now_hhmm, quiet_start, quiet_end)
    )
```

- [ ] **Step 4: Rewire the route to call the predicate**

Replace the condition at `src/muhideen/api/app.py:822-831`:

```python
        adhan_url: str | None = None
        if _adhan_playback_allowed(
            enabled=settings.adhan_audio_enabled,
            next_prayer=event_dto.next_prayer,
            muted_prayers=settings.adhan_muted_prayers,
            now_hhmm=event_dto.now.strftime("%H:%M"),
            quiet_start=settings.quiet_hours_start,
            quiet_end=settings.quiet_hours_end,
        ):
```

The `try: resolve_adhan_path ...` block beneath stays exactly as is.

- [ ] **Step 5: Run the new tests plus the neighboring suites**

Run: `uv run pytest tests/unit/test_adhan_audio.py -v`
Expected: all PASS (old + new)

Run: `uv run pytest tests/test_display_from_files.py tests/e2e/test_public_api.py -q`
Expected: all PASS (route behavior unchanged by the verbatim move)

- [ ] **Step 6: Commit**

```bash
git add src/muhideen/api/app.py tests/unit/test_adhan_audio.py
git commit -m "test(audio): extract _adhan_playback_allowed, pin None next-prayer (#60)"
```

### Task 4: Full gate and PR

- [ ] **Step 1: Run the full gate**

Run: `uv run pytest -q`
Expected: all green (baseline: everything green on this branch before Task 1)

Run: `uv run ruff format --check . && uv run ruff check .`
Expected: clean

- [ ] **Step 2: Final review of the diff**

Run: `git log --oneline main..HEAD && git diff main --stat`
Expected: branch commits = design-spec + plan + task commits; diff touches only `src/muhideen/api/app.py` (import + predicate + call-site) and `tests/unit/test_adhan_audio.py` (2 appended tests)

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin test/adhan-audio-edge-pins-58-59-60
```

PR body closes #58 and #60 with the recorded decisions (keep never-quiet, pin passthrough) and notes the zero-behavior-change predicate move. Open with `gh pr create` (or push and open via web) — whichever the engineer prefers; the body must list the two closed issues and the gate evidence.
