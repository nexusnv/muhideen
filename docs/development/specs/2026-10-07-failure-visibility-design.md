# Failure visibility (#63, #86) + playlist budget note (#72) — design

Date: 2026-10-07 | Branch: `fix/failure-visibility-63-86-72`

Closes #63, #86, #72 in one PR. Decisions recorded on the issues
2026-10-06/07: tick signal + slate copy (#63 + #86, 404 kept — no
contract change); document open-window equivalence, no code change (#72).

## 1. Scope

In: `tick()` bare-tick + exception log on unexpected errors; SSE
`stage="error"` arm for unexpected errors; no-coords guidance copy on
the display 404 slate + deployment section; open-window repeat
equivalence docstring + pin test.
Out: validation-boundary changes, status-code changes, playlist math
changes. Behavior changes: unexpected errors now wake streams as
`stage="error"` instead of killing them (intended); all else pinned.

## 2. Prod changes

- `src/muhideen/engine/engine.py` `tick()`: on unexpected
  non-`MuhideenError`, publish the bare tick and log at exception level,
  then re-raise (same shape as the `MuhideenError` arm; `_run_ticker`
  already survives everything and keeps ticking).
- `src/muhideen/api/app.py` SSE stage mapping (`:344-367`): add
  `except Exception` → error log + `stage = "error"` after the
  `MuhideenError` arm, so the open stream degrades instead of the
  generator raising.
- `src/muhideen/api/app.py` display route `except ScheduleError` arm
  (`:788-794`): when `settings.lat is None and settings.lon is None`,
  the 404 slate message carries guidance copy (set coordinates in
  `muhideen.json` or sync the zone timetable). Status stays 404.
- `src/muhideen/domain/stage.py` `resolve_stage` docstring: one sentence
  — open-window (no start bound) `repeat` never exhausts and is
  equivalent to `indefinite`, by intent.

## 3. Tests

- Tick unit test: unexpected error from `next_event` → bare tick
  published + exception propagates (find existing tick tests via grep).
- Stream test: unexpected error in stage resolution → `stage="error"`
  payload, stream stays open.
- Route test: no-coords settings + empty timetable → 404 HTML contains
  the guidance copy (coords-present case keeps bare "No schedule").
- Stage pin test (#72): open-window `repeat` + `max_cycles` playlist
  still occupies well past the budget (`tests/unit/test_domain_stage.py`
  `_playlist` helper takes `start=None`; mirror the `max_cycles` test
  style at `:381-392`).

## 4. Docs

`docs/deployment.md`: one section for the no-coords + fetch-fail
failure (slate copy + operator remedy: enter coordinates or retry sync).

## 5. Acceptance

New tests fail-before/pass-after (except the #72 pin, which passes
throughout as a current-behavior guard); full gate green; PR closes
#63, #86, #72.
