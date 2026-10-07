# Adhan-audio edge pins (#58, #59, #60) — design

Date: 2026-10-07 | Branch: `test/adhan-audio-edge-pins-58-59-60` | Approach: A (predicate helper)

Closes #58, #59, #60 in one PR. Zero behavior change: every pin asserts
current behavior, and the single prod touch is a verbatim move of the
existing display-route condition into a testable pure predicate.

## 1. Scope

In: three unit-test pins for decided-keep-current semantics
(GitHub decisions recorded 2026-10-06).
Out: #91 follow-ups, #61 (display domain), any `_is_mp3` tightening,
any quiet-hours contract change.

## 2. Prod change (`src/muhideen/api/app.py`)

Extract the inline effective-audio condition at the display route
(currently `src/muhideen/api/app.py:825` area) into a module-level pure
predicate next to `_in_quiet_hours` (`:164`):

```python
def _adhan_eligible(next_prayer, muted_prayers, now_hhmm, quiet_start, quiet_end, has_file) -> bool: ...
```

Body = the existing conjunction, moved verbatim
(`next_prayer not in muted` + `not _in_quiet_hours(...)` + file-exists);
the route calls it with the same arguments. No caller-visible change.

## 3. Tests (`tests/unit/test_adhan_audio.py`)

Existing file, existing `pytestmark = pytest.mark.unit` style; each test
cites its issue number.

- #58: `start == end` yields never-quiet, plus overnight (`22:00–06:00`)
  and same-day sanity rows through `_in_quiet_hours`.
- #59: synthetic header vectors through `_is_mp3` — `ID3` magic accept,
  valid sync (`0xFF 0xFB`) accept, reserved-bit pattern (`0xFF 0xE0`)
  accepted-today (pinned, not tightened), short/garbage reject.
  Inline bytes; no fixture files (stdlib-only constraint stands).
- #60: `next_prayer=None` → eligible through `_adhan_eligible`
  (safe default; the overlay only renders during ADHAN, which always
  has a next prayer). Also pin one muted-prayer negative for contrast.

## 4. Acceptance

- New tests pass; full gate green (`pytest -q`, `ruff` clean).
- Existing audio/route tests untouched and green.
- Diff is additive except the verbatim predicate move; no docs,
  migration, or browser check needed. PR body closes #58, #59, #60.
