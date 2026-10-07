# Timezone hardening (#56, #57) — design

Date: 2026-10-07 | Branch: `fix/timezone-hardening-56-57`

Two one-liners, one PR. Closes #56 and #57. Supersedes PR #83
(same guard change; close as superseded with credit).

## 1. Scope

In: `TypeError` in the `Settings` timezone guard (#56); single-owner
`TIMEZONE_DEFAULT` import in `file_models.py` (#57).
Out: everything else. No behavior change beyond `None` → `ValueError`.

## 2. Prod changes

- `src/muhideen/core/values.py:406`: guard tuple gains `TypeError`,
  so `Settings(timezone=None)` raises `ValueError("unknown timezone: …")`
  instead of escaping as `TypeError` (keeps the `ValueError→422/ConfigError`
  contract airtight).
- `src/muhideen/adapters/file_models.py:19,95`: add `TIMEZONE_DEFAULT`
  to the existing `core.values` import; `Masjid.timezone` default becomes
  `TIMEZONE_DEFAULT`. Last hardcoded *config default* in `src/` (DTO axis
  already gone, `install.sh` never hardcoded it; the runtime fallback
  `_PROD_TZ` in `api/app.py` is a separate axis, out of scope).

## 3. Tests

- Extend `test_timezone_defaults_to_kl_and_rejects_unknown`
  (`tests/unit/test_core_values.py:655`) with a `timezone=None` →
  `ValueError match="unknown timezone"` case (pass `None` type-cleanly,
  e.g. via `**kwargs`, so strict checkers stay quiet).
- Pin `Masjid().timezone == TIMEZONE_DEFAULT` in the file-model schema
  tests.

## 4. Acceptance

New/changed tests pass; full gate green (`pytest -q`, `ruff` clean).
PR closes #56 + #57; follow-up comment closes #83 as superseded.
No docs or migration needed.
