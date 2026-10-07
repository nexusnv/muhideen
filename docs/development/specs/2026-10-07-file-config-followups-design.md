# File-config follow-ups (#95, #97) — design

Date: 2026-10-07 | Branch: `fix/file-config-followups-95-97`

Closes #95 and #97 in one PR. Decisions recorded on the issues
2026-10-07: in-process lock + documented last-writer-wins (#95);
keep whole-file 503 + explicit docs (#97).

## 1. Scope

In: `threading.Lock` serializing `FileSettingsRepo.save()` (#95) with a
threaded race test, plus LW-wins documentation; one explicit docs
sentence for the pins-error read path (#97).
Out: cross-process file locking, optimistic concurrency, split
settings/pins validation boundary. No behavior change except
same-process save serialization.

## 2. Prod change (`src/muhideen/adapters/file_config.py`)

`FileSettingsRepo.__init__` gains `self._lock = threading.Lock()`,
mirroring the `FilePrayerRepo` precedent (`:592-595`, same comment
shape: in-process writers cannot lose updates; cross-process still
relies on tmp+rename, last writer wins, readers never tear).
`save()`'s read-modify-write body runs under `with self._lock`.
`threading` is already imported (`:17`). No signature changes.

## 3. Tests (`tests/test_file_repos.py`)

Threaded concurrent-saves test: N threads save distinct settings
through one repo instance; assert the file always parses, carries one
writer's mapped values intact, and no update is silently torn. Read the
file first for existing save-test placement and style.

## 4. Docs

- `ARCHITECTURE.md` + `docs/deployment.md`: operator concurrent edits
  are last-writer-wins (same contract as the prayer buffer).
- `docs/deployment.md` pins section: explicit read-path sentence — a
  pins-file error fails the whole load → 503 by design, same as an
  invalid main-config edit (watcher path already keeps last-good).

## 5. Acceptance

Race test fails without the lock (or is shown to be flaky without it),
passes with it; full gate green; PR closes #95 + #97.
