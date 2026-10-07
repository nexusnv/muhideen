# File-config follow-ups Implementation Plan (issues #95, #97)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Serialize same-process `FileSettingsRepo.save()` with a lock plus race test and LW-wins docs (#95); document the pins-error read-path 503 contract (#97).

**Architecture:** Adapter-layer only (`adapters/file_config.py` + file-repo tests + two docs). Mirrors the existing `FilePrayerRepo` lock precedent. No DTO, domain, engine, API, or validation-boundary changes.

**Tech Stack:** Python 3.11+, uv, pytest (unit), ruff, strict pyright, import-linter.

**References:** Issues #95, #97; `src/muhideen/adapters/file_config.py:279-306` (`FileSettingsRepo`, lockless `save`), `:592-595` (prayer-repo lock precedent); `docs/deployment.md:195-210` (pins section); `ARCHITECTURE.md:99,132-135` (config/buffer rows).

**Branch:** `fix/file-config-followups-95-97` (from `main`)

---

## File Structure

- Modify: `src/muhideen/adapters/file_config.py` — `FileSettingsRepo.__init__` gains the lock; `save()` body runs under it.
- Test: `tests/test_file_repos.py` — threaded concurrent-saves race test (read file first for save-test placement/style).
- Docs: `ARCHITECTURE.md` (config row LW-wins note), `docs/deployment.md` (pins-section read-path sentence + LW-wins note).

---

### Task 1: Lock + race test + LW-wins docs (#95)

**Files:** `src/muhideen/adapters/file_config.py`, `tests/test_file_repos.py`, `ARCHITECTURE.md`, `docs/deployment.md`

- [ ] Read `tests/test_file_repos.py` save-test area and `FileSettingsRepo.save()` fully; match placement/style.
- [ ] Add failing test: threaded concurrent saves through one `FileSettingsRepo` (e.g. 8 threads × distinct `masjid_name` round-trips via save→load); assert the file always parses and carries intact mapped values. Run: `uv run pytest tests/test_file_repos.py -k concurrent_save -v` → Expected: FAIL without the lock (flaky loss/torn read) — run 3× to observe; if green without the lock, force the interleave with a `threading.Barrier` on file-read so the race is deterministic, then FAIL.
- [ ] Implement: `self._lock = threading.Lock()` in `FileSettingsRepo.__init__` (same comment shape as the prayer-repo precedent) + `with self._lock:` around `save()`'s read-modify-write. Verify: same command → PASS consistently (5×); `uv run pytest tests/test_file_repos.py -q` → PASS.
- [ ] Docs: one LW-wins sentence each in `ARCHITECTURE.md` (config row) and `docs/deployment.md` (operator concurrent edits are last-writer-wins).
- [ ] Commit: `git add <4 files>` → `git commit -m "fix(config): serialize FileSettingsRepo.save() with in-process lock (#95)"`.

### Task 2: Pins-error read-path docs sentence (#97)

**Files:** `docs/deployment.md` (pins section ~:195-210)

- [ ] Append one explicit sentence to the pins paragraph: a pins-file error fails the whole config load and the surface serves 503 by design — same contract as an invalid main-config edit (distinct from the watcher path, which keeps last-good on bad edits). Verify: `rg -n "503" docs/deployment.md` shows the sentence in the pins section.
- [ ] Commit: `git add docs/deployment.md` → `git commit -m "docs: state pins-error read-path 503 contract (#97)"`.

### Task 3: Full gate and PR

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → Expected: all green (fix formatting in a `style:` commit if the checker flags branch files; BLOCKED on anything outside branch files).
- [ ] Review: `git log --oneline main..HEAD && git diff main --stat` → only Task 1/2 files + spec/plan docs.
- [ ] Push, open PR titled `fix(config): save lock + pins-error docs (#95, #97)`, body cites both recorded decisions + gate evidence, `Closes #95, #97`.
