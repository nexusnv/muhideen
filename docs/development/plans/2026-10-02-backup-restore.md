# One-click backup export/restore + log viewing — Implementation Plan (issue #41)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** A volunteer admin can export the whole installation (DB + media) in one click, restore it onto replacement hardware, and read service logs from the admin UI — additive API, stdlib-only, tested round-trip with corrupt-archive rejection.

**Architecture:** New `adapters/backup.py` (zip bundle build/validate via stdlib `zipfile`: `muhideen.db` from `backup_to` + `media/` tree; member allowlist + size caps; online restore into the live DB via `sqlite3.Connection.backup()` after `migrate()` on the staged copy; atomic media dir swap) + `adapters/logs.py` (journalctl reader with injectable runner mirroring the time-sync probe pattern); admin-only `POST /api/backup/export` (zip download), `POST /api/backup/restore` (base64 zip, validated), `GET /api/logs` (lines array or available:false); admin System section UI; fixtures + contract + deployment docs + changelog.

**Tech Stack:** Python 3.11+ stdlib (`zipfile`, `sqlite3`, `subprocess`, `tempfile`), FastAPI, pytest markers `unit/contract/integration/e2e`.

**References:** Issue #41; `src/muhideen/adapters/sqlite_repo.py:564-572` (`backup_to` VACUUM INTO); `src/muhideen/adapters/time_sync.py:24-30,42-56` (subprocess runner-injection precedent); `src/muhideen/api/app.py:1136-1192` (base64 upload pattern), `app.py:461-465` (media_dir), `app.py:817-831` (admin gating); `packaging/muhideen.service:8-14` (stdout → journald, service name `muhideen`); `update.sh:62-82` (pre-update backup + recovery hints); `src/muhideen/views/templates/admin/settings.html:293-312` (System section), `src/muhideen/static/admin.js` (save/upload patterns); `api/fixtures/playlist-image-upload.json` (upload fixture shape).

**Branch:** `feature/backup-restore` (cut when approved; base on `main`)

---

## Background the implementer needs

### Locked semantics

- Bundle layout (fixed): `{muhideen.db, media/<uploads tree>}`. Filenames inside the zip are fixed (`muhideen.db` at root, everything else under `media/`); restore rejects anything else (absolute paths, `..`, symlinks, non-listed roots) — traversal-safe by construction, not by sanitization.
- Caps: total archive ≤256MB, per-member ≤64MB, member count ≤512 (media is already capped upstream: 50 images × 5MB + 10MB adhan; caps are defense-in-depth, documented in contract).
- DB restore is online: validate staged zip → extract staged db → `migrate()` it (rejects unknown versions) → `staged.backup(live)` inside the live `write()` transaction → atomic media dir swap (`os.replace` of a fully-staged temp dir). No restart required; no boot-staging logic. Verify `Database.write()` yields a live `sqlite3.Connection` usable as backup target before assuming (read the class first).
- Export contains password hashes (`users` table) — docs mark the archive as secret (same handling as the DB file itself). No redaction (a redacted backup cannot restore login).
- Logs: `journalctl -u muhideen --no-pager -n <lines>` (lines default 100, max 1000, admin-only). No journal (dev machines, probes failing) → `200 {"available": false, "hint": "journalctl ... "}` — never 500 for absent logs. Runner injectable (`runner: Callable[[list[str]], str] | None`) + timeout 10s, mirroring `time_sync.py`.
- Restore request is base64 JSON (`{archive_base64}`) — consistent with the no-multipart footprint decision (playlist/adhan precedent). 256MB base64 ≈ 341MB JSON — document the practical limit; the pre-decode length bound pattern from the adhan route applies (mirror it).
- Non-goals: scheduled/auto backups (update.sh + deployment cron guidance only), selective restore, log filtering/search (plain tail), encrypted archives, multipart uploads, touching scheduler/engine/display.

## File Structure

- Create: `src/muhideen/adapters/backup.py` — build/validate/restore bundle
- Create: `src/muhideen/adapters/logs.py` — journalctl reader
- Test: `tests/unit/test_backup.py` + `tests/unit/test_logs.py` — round-trip, traversal/oversize/corrupt rejection, runner doubles
- Modify: `src/muhideen/api/app.py` — 3 routes (export download via FileResponse/StreamingResponse + temp cleanup; restore; logs)
- Modify: `src/muhideen/views/templates/admin/settings.html` — System section: Export button, Restore file input + button + status, Logs `<pre>` + refresh + line count
- Modify: `src/muhideen/static/admin.js` — wiring (download via anchor/blob, base64 restore POST, logs GET render); DEFAULTS untouched (no settings-DTO change)
- Fixtures: `api/fixtures/backup-restore.json` (restore request/response shapes) + `api/fixtures/logs.json` (both available true/false shapes); export is `application/zip` — documented shape only (filename pattern), no JSON fixture
- Test: route e2e (admin auth, export→import round-trip through HTTP, corrupt rejection, logs true/false)
- Docs: `docs/api-contract.md` (3 routes), `docs/deployment.md` (backup procedure: export/restore steps, secret handling, no-scheduler scope), `CHANGELOG.md` Added entry

No migration, no engine/scheduler change, no settings-DTO change, no locales change.

---

### Task 1: Bundle adapter + unit tests

**Files:** Create `src/muhideen/adapters/backup.py`; create `tests/unit/test_backup.py`

- [ ] Failing tests: build from a fixture source dir (small db file + 2 media files incl. nested) → zip contains exactly `{muhideen.db, media/...}`; validate rejects traversal (`../x`, `/abs`, symlink member), oversize member, oversize total, non-zip bytes, missing `muhideen.db`; restore round-trip (validate → stage → migrate-stub → backup into live tmp SQLite → rows present; media swapped atomically). Fakes: real tmp SQLite via `sqlite3` + a stub migrate callable (adapter takes `migrate_fn`, defaulting to the real `migrate` — check its import weight first; if heavy, default `None` = skip migrate and let the route pass the real one). Run: `uv run pytest tests/unit/test_backup.py -q` → Expected: FAIL (no module).
- [ ] Implement: `MAX_ARCHIVE_BYTES = 256MB`, `MAX_MEMBER_BYTES = 64MB`, `MAX_MEMBERS = 512`; `build_backup(db, media_dir, dest_zip)` (uses `backup_to` for the db member — check `backup_to` dest-exists behavior: fails if exists, so build into a fresh tmp path); `validate_archive(path)` returning member list or raising `ValueError`; `restore_backup(db, media_dir, staged_zip, *, migrate_fn)` (validate → extract to tmp → migrate staged → `staged.backup(live)` in `write()` → atomic media swap via temp dir + `os.replace`; cleanup tmps in `finally`). Verify: same file → PASS.
- [ ] Lint touched files.

### Task 2: Logs adapter + unit tests

**Files:** Create `src/muhideen/adapters/logs.py`; create `tests/unit/test_logs.py`

- [ ] Failing tests: fake runner returning 3 journal lines → `read_logs(lines=100)` returns them; runner raising `OSError` → `available=False` with hint; `lines` clamped to 1..1000 (0 → 1? clamp, don't error — decide: clamp upper, floor lower at 1). Run → Expected: FAIL (no module).
- [ ] Implement: mirror `time_sync.py` runner pattern (`_JOURNALCTL = ["journalctl", "-u", "muhideen", "--no-pager", "--output=short"]`, `runner` injectable, 10s timeout, unit name constant). `read_logs(*, lines=100, runner=None) -> dict` returning `{"available": True, "lines": [...]}` or `{"available": False, "hint": ...}`. Verify → PASS.
- [ ] Lint touched files.

### Task 3: Routes + contract + fixtures

**Files:** `src/muhideen/api/app.py`, `api/fixtures/backup-restore.json`, `api/fixtures/logs.json`, `docs/api-contract.md`, route tests (new `tests/e2e/test_backup.py` reusing authed-client + tmp media patterns from `test_admin_playlists.py`/`test_manual_day.py` — read fixtures first)

- [ ] Failing tests: export as admin → 200 `application/zip` with content-disposition filename + re-importable (round-trip through restore → settings row present); restore corrupt (text bytes / traversal zip) → 400; restore oversize base64 → 413 (pre-decode bound mirroring adhan route); anonymous → 401 all three; logs with stubbed runner → lines shape; logs without journal → available:false shape (monkeypatch runner to raise). Fixture parity tests for the two JSON fixtures. Run → Expected: FAIL (404 no routes).
- [ ] Implement: `POST /api/backup/export` (admin; build zip to `tempfile` + `FileResponse(..., filename=muhideen-backup-<ts>.zip)` + `BackgroundTask` cleanup — check starlette availability: FastAPI FileResponse supports `background=`; verify import path first); `POST /api/backup/restore` (admin; `{archive_base64}` DTO, pre-decode bound, `restore_backup` with real migrate, `ValueError` → 400); `GET /api/logs` (admin; `lines` query 1..1000, route passes app's log runner — AppDeps gains optional `log_runner`? Check AppDeps pattern: `time_sync` probe is injected; mirror by injecting nothing and letting the route use default runner (tests monkeypatch module runner) — simplest: module-level indirection testable via monkeypatch, no AppDeps change). Contract section (all three incl. export download semantics + secret handling + size caps). Verify: new test file + `tests/contract -q` → PASS.
- [ ] Lint touched files (`ruff` + `lint-imports` — new adapter imports must respect layers: api→adapters allowed, verify).

### Task 4: Admin UI (System section)

**Files:** `src/muhideen/views/templates/admin/settings.html`, `src/muhideen/static/admin.js`, `tests/e2e/test_admin_settings.py`

- [ ] Failing test: `/admin/settings` HTML contains export/restore/logs control ids; served `admin.js` references the three endpoints. Run → Expected: FAIL.
- [ ] Implement: System section additions (Export button → `fetch POST` → blob download via temp anchor; Restore file input → base64 → POST → status line incl. corrupt errors; Logs `<pre>` + lines input + Refresh → render or unavailable hint). DEFAULTS untouched. Verify → PASS (`node --check`).
- [ ] Lint test file.

### Task 5: Deployment procedure + changelog + full gate

- [ ] `docs/deployment.md`: backup procedure section (when to export, restore steps onto replacement hardware, archive-is-secret, what is NOT included: scheduler state/sessions, logs not in bundle). CHANGELOG `[Unreleased] Added` bullet (no dev-plan filename ref).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #41 → Tasks 1–4 (export/restore/logs + UI), Task 5 (procedure docs). Scheduled backups, selective restore, log search, encryption, multipart excluded with reasons.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task; grep-first steps named.
3. Type consistency: base64 JSON in/out like playlist/adhan routes; `{ok}`/`{available}` shapes documented in fixtures; zip layout fixed strings shared via adapter constants (route imports names, not literals — verify).
4. Momus dry gate: `sqlite_repo.py:564-572` / `time_sync.py:24-56` / `app.py:1136-1192,461-465,817-831` / `muhideen.service:8-14` / `update.sh:62-82` / `settings.html:293-312` verified on disk; order fixed (adapters → routes → UI → docs); each task red→green. Engine/scheduler/settings-DTO/locales untouched.
