# Admin API surface (token-gated file-config) Implementation Plan

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Implement the full token-gated admin FastAPI surface from `docs/superpowers/specs/2026-10-08-admin-api-design.md` so a frontend admin panel can ship without restoring DB/sessions.

**Architecture:** API + adapter layers only (`api/app.py` + new `api/admin.py`, `adapters/file_config.py`, new `adapters/media_store.py`, `service.py`). No domain/engine/core changes except via existing seams (`_settings_from_config`, `validate_pins`, `parse_window`, `normalize_adhan_rel`). No new SSE channel, no sessions, `workers=1` preserved.

**Tech Stack:** Python 3.11+, uv, FastAPI-sync + Uvicorn 1 worker, ruff, strict pyright, import-linter, pytest (unit/contract/e2e). Two new runtime deps: `Pillow` (image verify + re-encode + `MAX_IMAGE_PIXELS`) and `python-multipart` (multipart parsing; reverses the prior no-multipart footprint per spec decision 3).

**References:** Spec `docs/superpowers/specs/2026-10-08-admin-api-design.md` §§1–4; `src/muhideen/api/app.py:201-214` (`AppDeps`), `:650-675` (docs/mount), `:926-1015` (`create_production_app`); `src/muhideen/service.py:19-56` (argparse, `workers=1`); `src/muhideen/adapters/file_config.py:179-211` (`_atomic_write_json`), `:292-297` (lock insufficiency note), `:312` (`save` surgery), `:651-664` (`validate_pins`), `:858-904` (`_playlist_from_file`), `:907-929` (`FilePlaylistRepo`); `src/muhideen/adapters/file_models.py:442-455` (`ConfigFile` alias), `:164-250` (`Schedule` validators); `src/muhideen/core/values.py:314-349` (`normalize_adhan_rel`), `:425-479` (overrides/mutes); `src/muhideen/domain/playlist_window.py:53-115` (`parse_bound`/`parse_window`); `src/muhideen/adapters/aladhan.py:192-243` (base_url/method are ctor config, lat/lon per-fetch); `tests/test_no_admin.py:75-118` (must be split); `docs/api-contract.md:1-10` (parity note); `tools/mock_api.py:1-69` (stdlib mock); `packaging/muhideen.service`, `install.sh`.

**Branch:** `feature/admin-api-surface` (from `main`)

---

## File Structure

- Create: `src/muhideen/api/admin.py` — `HTTPBearer(auto_error=False)` dependency (disabled-check first → `503`, then `compare_digest` → `401` + `WWW-Authenticate`), shared-lock write helper, all admin routes (config PATCH/GET/validate, playlists/items/preview, displays, manual-days, media, backup, logs), per-route `ValueError` → `422 [{loc,msg}]` mapping.
- Modify: `src/muhideen/api/app.py` — `AppDeps` gains `admin_token: str | None` + `write_lock: threading.Lock`; unconditional `/media` mount + `mkdir -p`; register admin routes; `GET /api/config` serializes `by_alias=True`; preview route registered before `{id}`.
- Modify: `src/muhideen/service.py` — `--admin-token-file` (default `Path(args.config).parent / "admin_token"`), read-once at boot (missing/blank/unreadable → `None` + warning), plumb into `create_production_app`.
- Modify: `src/muhideen/api/app.py:create_production_app` signature — accept `admin_token_file`, build lock, pass through.
- Modify: `src/muhideen/adapters/file_config.py` — list-capable `_atomic_write_json_list` (same tmp+rename+fsync, `json.dumps(list)`).
- Create: `src/muhideen/adapters/media_store.py` — basename sanitization, bounded streaming reads, Pillow verify/re-encode, tmp+rename binary writes, `ENAMETOOLONG` → `ValueError`.
- Modify: `pyproject.toml` + `uv.lock` — add `Pillow` and `python-multipart` (record licenses in commit message).
- Modify: `install.sh` + `packaging/muhideen.service` — generate `admin_token` once if absent (`token_urlsafe(32)`, `0600`, `chown muhideen`, never clobber); unit passes `--admin-token-file` explicitly.
- Modify: `tools/mock_api.py` — gated-shape parity (`dev-admin-token`, `401`/`503` identical shapes, canned `422` fixtures only).
- Create: `api/fixtures/admin-masjid-*.json`, `admin-schedule-*.json`, `admin-playlist-*.json`, `admin-display-*.json`, `admin-pin-*.json`, `admin-media-*.json` — one `200`/`201` + one `422` sample per family (mock + contract inputs).
- Test: `tests/test_admin_auth.py`, `tests/test_admin_config.py`, `tests/test_admin_playlists.py`, `tests/test_admin_displays.py`, `tests/test_admin_manual_days.py`, `tests/test_admin_media.py`, `tests/test_admin_backup_logs.py` — all pinned-`FakeClock`, `TestClient` over tmp config (mirror `tests/test_no_admin.py:39-72` fixture shape).
- Modify: `tests/test_no_admin.py` — split kept-`404` vs newly-gated-`401`/`200` matrix.
- Modify: `docs/api-contract.md` + `api/fixtures/` + `CHANGELOG.md` — new routes, error codes, multipart shapes (contract discipline: same PR).

---

### Task 1: Auth + cross-cutting (token, lock, errors, mount)

**Files:** `src/muhideen/service.py`, `src/muhideen/api/app.py`, `src/muhideen/api/admin.py`, `tests/test_admin_auth.py`

- [ ] Add failing tests in `tests/test_admin_auth.py`: missing-token boot → gated write/validate/logs/export are `503` with `"admin writes disabled"` even with a Bearer header, public reads stay `200`; configured token → missing/wrong/empty Bearer is `401` + `WWW-Authenticate: Bearer`; `HTTPBearer` never `403`; gated audit log line contains peer/method/path/status and no token. Run: `uv run pytest tests/test_admin_auth.py -q` → Expected: FAIL (no routes/fields yet).
- [ ] Implement: `--admin-token-file` default `Path(config).parent / "admin_token"`, read-once-or-`None` (blank → `None`); `AppDeps.admin_token: str | None` + `write_lock: threading.Lock`; `require_admin` dependency with disabled-first ordering and `secrets.compare_digest`; sync-`def` writes holding the shared lock around read+merge+validate+rename; unconditional `/media` mount (`mkdir -p` first, `check_dir` kwarg only if pinned Starlette supports it); `loc` convention `["body", <field>, ...]` for mapped `ValueError`s. Verify: same command → PASS; `uv run pytest tests/test_no_admin.py -q` still PASS (routes not yet added).
- [ ] Commit: `git add src/muhideen/service.py src/muhideen/api/app.py src/muhideen/api/admin.py tests/test_admin_auth.py` → `git commit -m "feat(admin): token auth, shared write lock, error shape, media mount"`.

### Task 2: Per-section config PATCH + GET + validate

**Files:** `src/muhideen/api/admin.py`, `tests/test_admin_config.py`, `api/fixtures/admin-masjid-*.json`, `api/fixtures/admin-schedule-*.json`
Depends on: Task 1.

- [ ] Add failing tests in `tests/test_admin_config.py`: section PATCH round-trips (masjid tz flip → `restart_required:true`, others `false`); nested `{aladhan:{base_url}}` preserves `method` (deep-merge) while overrides map replaces; blank `name`/`zone`/`jakim.zone`/`manual_days_file` → `422`; `by_alias` GET carries `$schemaVersion`; unknown-tz/boundary-override-key/bad `fixed` rule → `422 [{loc,msg}]` (not `500`); sibling-invariant enforced (provider switch without required fields → `422`). Run: `uv run pytest tests/test_admin_config.py -q` → Expected: FAIL.
- [ ] Implement: all-optional partials with strip-then-reject-blank; raw-dict surgery per section + full-file `ConfigFile.model_validate` + `_settings_from_config` + `validate_pins`-where-pins before rename; `restart_required` only for tz change and effective-sync-client change (`sync_provider` or `aladhan.*` while provider is `aladhan`); section-validate merged with the same depth rule, dry-run. Verify: same command → PASS; `uv run pytest tests/test_admin_auth.py tests/test_admin_config.py -q` → PASS.
- [ ] Commit: `git commit -m "feat(admin): per-section config PATCH, GET, validate"`.

### Task 3: Playlists + items + preview

**Files:** `src/muhideen/api/admin.py`, `tests/test_admin_playlists.py`, `api/fixtures/admin-playlist-*.json`
Depends on: Task 1.

- [ ] Add failing tests in `tests/test_admin_playlists.py`: `POST` requires `id`+`title` (blank → `422`), duplicate `id` → `409`; PATCH forbids `id` rename and any `items` key → `422`; window/anchor grammar via `_playlist_from_file` + `parse_window` (boundary anchor → `422`); `cycle_mode` pairing → `422`; item POST duplicate `sort_order` → `409` vs malformed → `422`, missing `sort_order` → `max+1` (empty → `0`), dangling `image_path` allowed; ambiguous-`sort_order` DELETE → `409`; `preview` registered before `{id}`, missing/naive `moment` → `422`, schedule-fail → `404`, config-fail → `503`. Run: `uv run pytest tests/test_admin_playlists.py -q` → Expected: FAIL.
- [ ] Implement: id grammar `1-64 [A-Za-z0-9_-]` on writes only (legacy lists as-is; slash-ids stay routing-`404`); legacy duplicate `sort_order` lists in file order. Verify: same command → PASS.
- [ ] Commit: `git commit -m "feat(admin): playlists, items, preview"`.

### Task 4: Displays PUT/PATCH/DELETE

**Files:** `src/muhideen/api/admin.py`, `tests/test_admin_displays.py`, `api/fixtures/admin-display-*.json`
Depends on: Task 1.

- [ ] Add failing tests in `tests/test_admin_displays.py`: `PUT` upsert (`201` create / `200` replace, `{}` → all-defaults reset); `PATCH` partial merge with `theme.<knob>=null` inherit; `name` blank → `422`, `language` closed literal → `422` on unknown, `dim_minutes_override` `5-60|null`; unknown id `PATCH`/`DELETE` → `404`. Run: `uv run pytest tests/test_admin_displays.py -q` → Expected: FAIL.
- [ ] Implement per spec §3 (no schedule/iqamah fork). Verify: same command → PASS.
- [ ] Commit: `git commit -m "feat(admin): display CRUD"`.

### Task 5: Manual-days GET/PUT/DELETE + validate

**Files:** `src/muhideen/api/admin.py`, `src/muhideen/adapters/file_config.py`, `tests/test_admin_manual_days.py`, `api/fixtures/admin-pin-*.json`
Depends on: Tasks 1–2.

- [ ] Add failing tests in `tests/test_admin_manual_days.py`: `GET` returns `{source, pins}`; ref-set-but-absent → `GET`/`DELETE` are `503` (never empty `pins: []`), `PUT` creates via `mkdir` + list-atomic-write; path date authoritative (body mismatch → `422`); `≥1` marker + `HH:MM` structural vs `validate_pins` buffer-completion (`422` trap message for pre-sync partial pins); upsert replace `200` never `409`; inline↔file exclusivity `422`. Run: `uv run pytest tests/test_admin_manual_days.py -q` → Expected: FAIL.
- [ ] Implement: source routing via `resolve_pins_path`/`manual_days_file_for_config`; new `_atomic_write_json_list` helper. Verify: same command → PASS.
- [ ] Commit: `git commit -m "feat(admin): manual-days CRUD and validate"`.

### Task 6: Media GET/POST/DELETE (multipart + Pillow + atomicity)

**Files:** `src/muhideen/adapters/media_store.py`, `src/muhideen/api/admin.py`, `tests/test_admin_media.py`, `pyproject.toml`, `uv.lock`
Depends on: Task 1.

- [ ] Add failing tests in `tests/test_admin_media.py`: `kind` routes destination (`adhan` root vs `image` `playlists/`, subdir ensured); basename flatten + reject list (empty/NUL/`?`/`#`/trailing-`/`/drive/`..`/overlong → `422`); `≤5MB` image / `≤10MB` audio enforced by bounded streaming (oversize → `413` without full buffering); Pillow open + re-encode (EXIF stripped, `MAX_IMAGE_PIXELS` guard, format preserved), mp3 frame-sync/ID3 weak check, kind/ext mismatch → `422`; overwrite → `200` vs new → `201`; `DELETE` unconditional (dangling refs allowed, no reference scan), unknown → `404`. Run: `uv run pytest tests/test_admin_media.py -q` → Expected: FAIL (deps/routes missing).
- [ ] Implement: add `Pillow` + `python-multipart` to `pyproject.toml`, `uv lock`; media writes via tmp-in-same-dir + rename (re-encode output included); `ENAMETOOLONG` mapped to `422`. Verify: same command → PASS.
- [ ] Commit: `git add pyproject.toml uv.lock src/muhideen/adapters/media_store.py src/muhideen/api/admin.py tests/test_admin_media.py` → `git commit -m "feat(admin): media upload/list/delete (Pillow + multipart, atomic writes)"` (body notes both licenses).

### Task 7: Backup export/restore + logs + mock + contract + device

**Files:** `src/muhideen/api/admin.py`, `tools/mock_api.py`, `install.sh`, `packaging/muhideen.service`, `docs/api-contract.md`, `api/fixtures/`, `tests/test_admin_backup_logs.py`, `tests/test_no_admin.py`, `CHANGELOG.md`
Depends on: Tasks 1–6.

- [ ] Add failing tests in `tests/test_admin_backup_logs.py`: export sums media before archiving (`413` over `50MB`), streams zip, relative paths only, corrupt buffer byte-copied; restore hardens zip-slip (absolute/`..`/symlink/drive-letter rejected), validate-all-before-touch (staged-buffer pins check), same-filesystem staging (`<config-dir>/.restore-*.tmp`, removed on success / kept on failure), rollback from staging backup, additive media with per-file tmp+rename; `GET /api/logs?lines=` default `200`, `1-1000` else `422`, argv-only `journalctl` with 5s timeout → `501` on timeout/unavailable; `tests/test_no_admin.py` split into kept-`404` vs newly-gated-`401`/`200`. Run: `uv run pytest tests/test_admin_backup_logs.py tests/test_no_admin.py -q` → Expected: FAIL.
- [ ] Implement: export manifest `{files:[{path,size_bytes,sha256}], exported_at}`; restore ordered renames + mixed-generation tolerance note; `install.sh` one-time `admin_token` generation + unit flag; mock `dev-admin-token` gated shapes with canned `422`s; update `docs/api-contract.md` + fixtures + `CHANGELOG.md`. Verify: same command → PASS; `uv run pytest -m contract -q` → PASS.
- [ ] Commit: `git commit -m "feat(admin): backup, logs, mock, contract, device wiring"`.

### Task 8: Full gate and PR

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → Expected: all green (coverage ≥95 via `uv run pytest --cov -q`); fix formatting in a `style:` commit if flagged.
- [ ] Review: `git log --oneline main..HEAD && git diff main --stat` → only Task 1–7 files + spec/plan docs; placeholder scan of this plan file → 0 hits.
- [ ] Push, open PR titled `feat(admin): token-gated file-config admin API`, body cites spec + gate evidence. Review gate: `muhideen-momus-review` on this plan, `muhideen-oracle-review` on the branch diff before handoff.
