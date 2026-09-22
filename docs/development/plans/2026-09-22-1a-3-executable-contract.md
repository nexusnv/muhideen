# Slice 1A-3 Executable Contract Implementation Plan

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: `muhideen-momus-review` (plan), `muhideen-oracle-review` (after impl).

**Goal:** Land Pydantic DTOs for all five contract surfaces (`prayer-day`, `next-event`, SSE events, `heartbeat`, `version`) plus a `create_app()` OpenAPI skeleton, with `tests/contract/` enforcing DTO ↔ `api/fixtures/` ↔ `docs/api-contract.md` ↔ OpenAPI parity so any drift fails CI — unblocking frontend slices 1B-1/1B-2.

**Architecture:** `src/muhideen/api/` only (DTO module + app skeleton with 501-stub handlers). DTOs map from `muhideen.core` value objects (`PrayerDay`, `NextEvent`) — no `domain/`, `engine/`, `adapters/` imports, no business logic, no DB, no network, no wall-clock reads. Handler bodies stay stubs until 1A-7; wire formats follow `docs/api-contract.md` exactly (uppercase `state`, `HH:MM` times, tz-aware ISO8601). `tools/mock_api.py` switches its version/heartbeat responses from inline literals to fixture files so the mock cannot drift from fixtures.

**Tech Stack:** Python 3.11+, uv, **pydantic v2** (added as a direct dependency — currently only transitive via fastapi), FastAPI-sync + Uvicorn 1 worker (app created, never served in this slice), ruff, strict pyright, import-linter, pytest (`contract` marker only — unit/integration untouched).

**References:** `docs/development/PHASES_AND_SLICES.md:38` slice 1A-3 row, `docs/api-contract.md:7-19` prayer-day, `:21-37` next-event, `:39-41` events, `:43-49` heartbeat, `:51-55` version, `:57-58` versioning, `api/fixtures/prayer-day.json:1`, `api/fixtures/next-event.json:1`, `api/fixtures/events-stream.txt:1-14`, `tests/contract/test_fixtures.py:3-4` (skeleton defers full parity to 1A-3), `src/muhideen/core/values.py:18-39` enums, `:42-53` `PrayerDay`, `:56-64` `NextEvent`, `src/muhideen/domain/__init__.py:8-17` domain exports (input to mappers' test vectors), `tools/mock_api.py:35-63` routes (`:49-50` inline version, `:57-60` inline heartbeat response), `pyproject.toml:19-25` dependencies (no pydantic), `:33-40` pytest markers, `:48-56` coverage `fail_under=95`, `:75-87` import-linter layers, `.github/workflows/ci.yml:18-24` gate (`uv sync --locked` + `pytest -q`), `ARCHITECTURE.md:45-46` API layer owns Pydantic DTOs, `:55` cross-boundary change = contract change, `:57-62` quality enforcement, `TESTING_STRATEGY.md:10` contract layer definition, `:17-20` pinned time + local test doubles, `PRD.md:154` Pydantic DTOs are the executable contract, `PRD.md:177-178` backend lands api + OpenAPI + fixtures first, `PRD.md:225` CI fails on OpenAPI/fixture drift, `PRD.md:304-312` §6.3 contract, `PRD.md:331` OTA reads `/api/version`, `docs/adr/0002-single-repo-api-first.md:10,18`, `CONTRIBUTING.md:58` contract changes ship fixtures + doc + changelog in one PR, `CONTEXT.md:71-75` Contract/Fixtures terms.

**Branch:** `feature/1a-3-executable-contract` (from `main`, already created at `803de2e`)

**Slice:** 1A-3 Executable contract. Entry: 1A-2 landed (`803de2e`), baseline `uv run pytest -q` = 76 passed, coverage 96.15%. Exit: `contract` green; drift fails CI (`.github/workflows/ci.yml:23` runs full `pytest -q`, markers always collected); `docs/api-contract.md` + fixtures updated together (enforced by `tests/contract/test_api_contract_doc.py`).

---

## File Structure

- Create: `src/muhideen/api/dto.py` — 8 Pydantic models: `PrayerDayDTO`, `NextEventDTO`, `StateEventDTO`, `TickEventDTO`, `ConfigUpdateEventDTO`, `HeartbeatRequestDTO`, `HeartbeatResponseDTO`, `VersionDTO`; wire-format aliases `StateLiteral`, `PrayerLiteral`, `TimeHHMM`; `SSE_PAYLOAD_MODELS` registry; `from_domain` mappers
- Create: `src/muhideen/api/app.py` — `create_app() -> FastAPI` with the 5 contract routes declared (`response_model` set, sync `def` stubs raising `HTTPException(501)`)
- Modify: `src/muhideen/api/__init__.py` — re-export the 8 DTOs + `create_app`
- Modify: `pyproject.toml` — add `pydantic>=2` to `[project] dependencies` (line 19 block); regenerate `uv.lock` via `uv lock`
- Create: `api/fixtures/heartbeat-request.json` — `{"id": "HALL-01"}` (mirrors `docs/api-contract.md:47`)
- Create: `api/fixtures/heartbeat-response.json` — `{"ok": true}`
- Create: `api/fixtures/version.json` — `{"version": "0.1.0", "api": "v1"}` (mirrors `docs/api-contract.md:54`, currently inline at `tools/mock_api.py:50`)
- Modify: `tools/mock_api.py` — serve `version.json` and `heartbeat-response.json` from fixture files instead of inline literals
- Modify: `docs/api-contract.md` — heartbeat section (`:43-49`) gains the response example (additive)
- Create: `CHANGELOG.md` — Keep a Changelog convention, `[Unreleased]` entry for this slice
- Modify: `tests/contract/test_fixtures.py` — replace key-set smoke tests (docstring `:3-4` says full parity is 1A-3) with DTO ↔ fixture round-trip + negative-case tests
- Test: `tests/contract/test_events_stream.py` — SSE stream parse + SSE DTO mapper tests
- Test: `tests/contract/test_openapi_parity.py` — `create_app().openapi()` ↔ DTO schema parity + 501-stub proof
- Test: `tests/contract/test_api_contract_doc.py` — `docs/api-contract.md` examples ↔ DTOs ↔ fixtures

No `domain/`, `engine/`, `adapters/`, `views/`, `themes/`, `migrations/` changes. Existing fixture payloads unchanged (1A-2 already corrected `next-event.json` to `12:20`).

---

### Task 1: Read-only DTOs — prayer-day + next-event parity

**Files:** `tests/contract/test_fixtures.py` (rewrite), `src/muhideen/api/dto.py` (create), `pyproject.toml` + `uv.lock` (modify)

- [ ] Rewrite `tests/contract/test_fixtures.py` (module-level `pytestmark = pytest.mark.contract`) with failing tests: `test_prayer_day_fixture_round_trips` — `PrayerDayDTO.model_validate(json.loads(fixture))` then `model_dump(mode="json") == fixture dict` (exact key-set both ways via `extra="forbid"`); `test_next_event_fixture_round_trips` — same for `api/fixtures/next-event.json`; `test_state_requires_uppercase_wire_value` — `{"state": "iqamah_countdown"}` raises `ValidationError` (core `PrayerState` values are lowercase, wire is uppercase per `docs/api-contract.md:37`); `test_times_require_hh_mm_format` — `"fajr": "05:45:00"` raises (fixture uses `05:45`); `test_naive_datetime_rejected` — `now: "2025-10-20T12:20:00"` (no offset) raises; `test_extra_field_rejected` — injected `"extra": 1` raises on both DTOs; `test_next_event_from_domain_matches_fixture` — with `KL = timezone(timedelta(hours=8))` build `core.NextEvent(state=PrayerState.IQAMAH_COUNTDOWN, now=datetime(2025,10,20,12,20,tzinfo=KL), next_prayer=PrayerName.DHUHR, adhan_at=datetime(2025,10,20,12,15,tzinfo=KL), iqamah_at=datetime(2025,10,20,12,30,tzinfo=KL), dim_until=datetime(2025,10,20,12,50,tzinfo=KL), stale=False)`, `NextEventDTO.from_domain(event)` dumps equal to fixture; `test_prayer_day_from_domain_matches_fixture` — build `core.PrayerDay(date(2025,10,20), "SGR01", time(5,45), time(6,55), time(12,15), time(15,30), time(18,5), time(19,25), ScheduleSource.JAKIM, fetched_at=datetime(2025,10,20,1,0,tzinfo=KL))`, `PrayerDayDTO.from_domain(day, stale=False)` dumps equal to fixture. No clock fixtures needed: every vector is a pinned literal string. Run: `uv run pytest tests/contract/test_fixtures.py -v` → Expected: FAIL (`ImportError`, no `muhideen.api.dto`).
- [ ] Add pydantic as a direct dependency (`uv add "pydantic>=2"` → edits `pyproject.toml:19-25`, regenerates `uv.lock`) and implement `src/muhideen/api/dto.py`: `model_config = ConfigDict(extra="forbid")` on every DTO; `TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]`; `StateLiteral = Literal["NORMAL", "PRE_ADHAN", "ADHAN", "IQAMAH_COUNTDOWN", "SALAH_DIM"]`; `PrayerLiteral = Literal["fajr", "syuruq", "dhuhr", "asr", "maghrib", "isha", "jumuah"]`; datetime fields get a field validator rejecting naive values; `PrayerDayDTO(date: date, zone: str, times: PrayerTimesDTO, source: ScheduleSource, stale: bool)` where `times` is a nested model with the six `TimeHHMM` fields (`fajr`, `syuruq`, `dhuhr`, `asr`, `maghrib`, `isha`), keyed exactly as `api/fixtures/prayer-day.json:1`; `NextEventDTO` mirrors `docs/api-contract.md:26-34` (7 fields: `state`, `now`, `next_prayer`, `adhan_at`, `iqamah_at`, `dim_until`, `stale`; datetimes `datetime | None`); `from_domain` maps enum → wire (`PrayerState.name` uppercase, `PrayerName.value` lowercase, `time.strftime("%H:%M")`). Verify: same pytest command → PASS; `uv sync --locked` (mirrors CI `ci.yml:18`) → resolves without error; `uv run pytest -q` → PASS (76 baseline + new).

**Depends on:** 1A-2 landed (`803de2e`); `core/values.py:42-64` shapes.

### Task 2: SSE event DTOs + stream parity

**Files:** `tests/contract/test_events_stream.py` (create), `src/muhideen/api/dto.py` (extend)

- [ ] Add failing tests: `test_events_stream_blocks_well_formed` — split `api/fixtures/events-stream.txt` on blank lines; each block has exactly one `event:` line and one `data:` line; event names ⊆ `{"state", "tick", "config-update"}` (per `docs/api-contract.md:41`); `test_events_stream_payloads_validate` — each block's `data` JSON validates against `SSE_PAYLOAD_MODELS[event_name]`; `test_stream_covers_all_three_event_names` — all three names present in the fixture; `test_state_event_omitted_targets_dump_without_null_keys` — `StateEventDTO(state="PRE_ADHAN", next_prayer="dhuhr", adhan_at=datetime(2025,10,20,12,15,tzinfo=timezone(timedelta(hours=8))))` with `model_dump(mode="json", exclude_none=True)` emits only the set keys (fixture state blocks `:4-11` omit `iqamah_at` etc.); `test_state_event_from_domain_excludes_absent_targets` — `core.NextEvent` with `iqamah_at=None`, `dim_until=None` → dump has neither key; `test_tick_event_from_domain_carries_now_and_state` — dump equals the `tick` fixture payload (`events-stream.txt:1-2`); `test_config_update_requires_non_empty_changed_list` — missing/empty `changed` raises. Run: `uv run pytest tests/contract/test_events_stream.py -v` → Expected: FAIL (ImportError for `StateEventDTO` etc.).
- [ ] Extend `dto.py`: `StateEventDTO` (flat, `state: StateLiteral` required; `now`, `next_prayer`, `adhan_at`, `iqamah_at`, `dim_until`, `stale` optional with `None` defaults, `extra="forbid"`), `TickEventDTO(now: datetime, state: StateLiteral)` (tz-aware validated), `ConfigUpdateEventDTO(changed: list[str]` with `min_length=1)`; `SSE_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {"state": StateEventDTO, "tick": TickEventDTO, "config-update": ConfigUpdateEventDTO}` plus `StateEventDTO.from_domain(event: NextEvent)` and `TickEventDTO.from_domain(event: NextEvent)` (callers dump with `exclude_none=True`). Verify: same command → PASS; `uv run pytest -q` → PASS.

**Depends on:** Task 1 (`StateLiteral`, tz validator reuse).

### Task 3: Heartbeat + version DTOs, fixtures, mock parity

**Files:** `tests/contract/test_fixtures.py` (extend), `src/muhideen/api/dto.py` (extend), `api/fixtures/heartbeat-request.json`, `api/fixtures/heartbeat-response.json`, `api/fixtures/version.json` (create), `tools/mock_api.py` (modify)

- [ ] Add failing tests: `test_version_fixture_round_trips` (`version.json` ↔ `VersionDTO`, round-trip exact); `test_heartbeat_request_fixture_round_trips` and `test_heartbeat_response_fixture_round_trips`; `test_heartbeat_rejects_empty_id` — `{"id": ""}` raises (`min_length=1`); `test_version_rejects_unknown_api_value` — `{"version": "0.1.0", "api": "v2"}` raises (only `"v1"` per `docs/api-contract.md:54`; bump rules live at `:57-58`); `test_all_contract_surfaces_have_fixtures` — the six fixture files (`prayer-day.json`, `next-event.json`, `events-stream.txt`, `heartbeat-request.json`, `heartbeat-response.json`, `version.json`) exist and parse (pins the fixture set slice 1A-3 promises for the five surfaces). Run: `uv run pytest tests/contract/test_fixtures.py -v` → Expected: FAIL (`FileNotFoundError` for the three new fixtures).
- [ ] Create the three fixture files (contents in File Structure; `heartbeat-request.json` mirrors `docs/api-contract.md:47`, `version.json` mirrors `:54`); implement `HeartbeatRequestDTO(id: str` with `min_length=1)`, `HeartbeatResponseDTO(ok: bool)`, `VersionDTO(version: str, api: Literal["v1"])`. Modify `tools/mock_api.py`: `do_GET` `/api/version` (line 49-50) reads `version.json` via existing `_read`; `do_POST` heartbeat (lines 55-60) responds `_read("heartbeat-response.json")`. Verify: pytest → PASS; mock QA: `uv run tools/mock_api.py &` then `curl -s http://127.0.0.1:8001/api/version` output byte-equals `api/fixtures/version.json`, `curl -s -X POST -d '{"id":"HALL-01"}' http://127.0.0.1:8001/api/displays/heartbeat` output byte-equals `api/fixtures/heartbeat-response.json` → Expected: both equal; `uv run pytest -q` → PASS.

**Depends on:** Task 1 (DTO base patterns).

### Task 4: `create_app()` OpenAPI skeleton + parity gate

**Files:** `tests/contract/test_openapi_parity.py` (create), `src/muhideen/api/app.py` (create), `src/muhideen/api/__init__.py` (modify)

- [ ] Add failing tests (module-level `pytestmark = pytest.mark.contract`), importing `from muhideen.api import ConfigUpdateEventDTO, HeartbeatRequestDTO, HeartbeatResponseDTO, NextEventDTO, PrayerDayDTO, StateEventDTO, TickEventDTO, VersionDTO, create_app` (exercises `__init__` re-exports): `test_openapi_declares_all_five_paths` — `create_app().openapi()["paths"]` contains `/api/prayer-day`, `/api/next-event`, `/api/events`, `/api/displays/heartbeat`, `/api/version`; `test_openapi_response_schemas_match_dto_schemas` — each JSON endpoint's response is `$ref: #/components/schemas/<DTOName>` (`PrayerDayDTO`, `NextEventDTO`, `HeartbeatResponseDTO`, `VersionDTO`) and POST heartbeat's requestBody is `$ref` to `HeartbeatRequestDTO`; component schema equals `DTOName.model_json_schema()` after rewriting `#/$defs/` refs to `#/components/schemas/` on both sides and dropping the `$defs` key; components include all five DTO names; `test_openapi_query_params_match_doc` — `prayer-day` declares query params `date` + `zone`, `next-event` declares `now` (per `docs/api-contract.md:7,21`); `test_events_route_declares_text_event_stream` — `/api/events` responses carry `text/event-stream` content; `test_stub_endpoints_return_501` — `TestClient(create_app())` GET prayer-day/next-event/events/version and POST heartbeat (body `{"id": "HALL-01"}`) each return 501 (proves the seam and covers stub bodies for the 95% gate). Run: `uv run pytest tests/contract/test_openapi_parity.py -v` → Expected: FAIL (`ImportError`, `create_app` not exported).
- [ ] Implement `src/muhideen/api/app.py`: `create_app()` builds `FastAPI(title="muhideen", version=importlib.metadata.version("muhideen"))`; five routes with sync `def` handlers raising `HTTPException(status_code=501, detail="handler wired in slice 1A-7")`; `response_model` set on the four JSON endpoints (`/api/events` uses `responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}}`); query params declared on prayer-day (`date: date`, `zone: str`) and next-event (`now: datetime`). Re-export the 8 DTOs + `create_app` from `src/muhideen/api/__init__.py`. Verify: same command → PASS; `uv run pytest -q` → PASS; `uv run pytest --cov=muhideen --cov-report=term-missing` → coverage ≥95 (baseline 96.15%, stub bodies now covered by the 501 test).

**Depends on:** Tasks 1-3 (all DTOs exist).

### Task 5: Doc ↔ fixture ↔ DTO parity (heartbeat response lands)

**Files:** `tests/contract/test_api_contract_doc.py` (create), `docs/api-contract.md` (modify `:43-49`)

- [ ] Add failing tests: `test_doc_json_examples_validate_against_dtos` — split `docs/api-contract.md` on `^## ` lines; map sections by title prefix to ordered DTO lists: `GET /api/prayer-day` → `[PrayerDayDTO]`, `GET /api/next-event` → `[NextEventDTO]`, `GET /api/events` → `[]` (stream lives only in `api/fixtures/events-stream.txt`; assert it has zero fenced ```json blocks), `POST /api/displays/heartbeat` → `[HeartbeatRequestDTO, HeartbeatResponseDTO]`, `GET /api/version` → `[VersionDTO]`; collect fenced ```json blocks per section in order and assert exact block counts then validate each against its DTO; `test_unmapped_doc_sections_carry_no_json_examples` — any other `## ` section containing a ```json block fails the test (forces a DTO mapping when a new example is added); `test_doc_examples_match_fixtures` — parsed doc blocks equal parsed fixture payloads for all five fixture-backed examples (prayer-day, next-event, heartbeat request, heartbeat response, version) — this pins "fixtures win on conflict" (`docs/api-contract.md:3`). Run: `uv run pytest tests/contract/test_api_contract_doc.py -v` → Expected: FAIL (heartbeat section yields 1 JSON block, test expects 2).
- [ ] Update `docs/api-contract.md` heartbeat section (`:43-49`): after the request example, add the response example `{"ok": true}` fenced as ```json (byte-content must equal `api/fixtures/heartbeat-response.json` once parsed) with one line of prose naming it the response body. Verify: same pytest command → PASS (heartbeat yields `[request, response]`, all examples match fixtures); `uv run pytest -q` → PASS.

**Depends on:** Task 3 (heartbeat-response fixture exists), Task 4 (`create_app` schema paths already pinned).

### Task 6: Changelog convention + full gate

**Files:** `CHANGELOG.md` (create), no source changes

- [ ] Create `CHANGELOG.md` Keep a Changelog format: `# Changelog`, `## [Unreleased]` with `### Added` (8 DTOs + `create_app()` OpenAPI skeleton; fixtures `heartbeat-request.json`/`heartbeat-response.json`/`version.json`; contract tests enforcing DTO ↔ fixtures ↔ doc ↔ OpenAPI parity; this changelog), `### Changed` (`docs/api-contract.md` heartbeat response body documented; `tools/mock_api.py` serves version/heartbeat from fixtures). This establishes the changelog convention 1A-2 deferred to this slice (`2026-09-22-1a-2-domain-pure-logic.md:97`) and satisfies `CONTRIBUTING.md:58` / `docs/adr/0002-single-repo-api-first.md:18`.
- [ ] Final verify — run in order, one Expected: `uv run pytest -m contract -q` → PASS (4 contract files, all tests green); `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q` → all green (pyright strict over new `api/` code; import-linter layers `api → engine → adapters → domain → core` unchanged); `uv run pytest --cov=muhideen --cov-report=term-missing` → ≥95; `uv sync --locked --all-extras` → resolves (matches `ci.yml:18`); paste all outputs in the PR description as the exit-gate evidence.

**Depends on:** Tasks 1-5.

---

## Detailed-Plan Checklist

1. **PRD refs:** §6.3 contract surfaces (`PRD.md:304-312`), Pydantic-as-contract (`PRD.md:154`), backend-first + normative fixtures (`PRD.md:177-178`), CI fails OpenAPI/fixture drift (`PRD.md:225`), heartbeat cadence FR-4.1 (`PRD.md:107`), SSE events FR-4.3/FR-2.3 (`PRD.md:109,90`), OTA version read (`PRD.md:331`).
2. **Contract diff:** `shape: additive only`. New: heartbeat response body `{"ok": true}` documented (was undocumented), 3 new fixture files, no renames/removals/semantic changes → no `/api/v2`, no version bump (`docs/api-contract.md:57-58` rules untouched). Existing prayer-day/next-event/events examples unchanged. Ships with `docs/api-contract.md` update + fixture files + `CHANGELOG.md` entry in the same PR (`CONTRIBUTING.md:58`).
3. **Changes with ownership:** backend-owned `src/muhideen/api/` (`dto.py`, `app.py`, `__init__.py`); shared `tools/mock_api.py`; cross-boundary `api/fixtures/` + `docs/api-contract.md` (`ARCHITECTURE.md:54-55`) shipped together with the changelog. No `domain/`, `engine/`, `adapters/`, `views/`, `themes/` changes; frontend untouched.
4. **Tests by layer + new invariants:** `contract` only — 4 files, ~28 tests; `unit` suite untouched (76 baseline green). New invariants: uppercase `state` on the wire, `HH:MM` time strings, tz-aware datetimes required, `extra="forbid"` on every DTO, SSE event-name allowlist, non-empty `changed` list, `api == "v1"`, non-empty heartbeat id, OpenAPI component schemas byte-equal DTO schemas, doc examples equal fixtures, stub endpoints return 501.
5. **Purity/import-linter impact:** none — `api/` is the top layer (`pyproject.toml:78-87`) and may import fastapi/pydantic/`muhideen.core`; DTO mappers import only `muhideen.core.values` (no `domain/` import, so no purity-scan change per `ARCHITECTURE.md:62`); `lint-imports` stays green.
6. **Docs touched:** `docs/api-contract.md` §heartbeat response example; `CHANGELOG.md` created (convention established). `CONTEXT.md` unchanged (Contract/Fixtures terms already defined `:71-75`); no ADR (no hard-to-reverse decision — DTO wire casing is test-pinned, reversible by contract bump rules).
7. **Rollback:** `git rm` `src/muhideen/api/{dto,app}.py`, 3 new fixtures, `CHANGELOG.md`, 3 new test files; `git checkout main -- src/muhideen/api/__init__.py tests/contract/test_fixtures.py docs/api-contract.md tools/mock_api.py pyproject.toml uv.lock`. No migration, no contract version bump, no fixture value revert (existing fixtures unchanged).
8. **Exit gate command output:** paste `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest -q`, `uv run pytest -m contract -q`, and the coverage line in the PR description; CI evidence is `.github/workflows/ci.yml:18-24` (runs the same gate incl. `pytest -q`, so contract drift fails CI).

## Out of Scope (later slices)

- Engine wiring and real handler bodies — `/api/events` streaming, heartbeat `last_seen` batching, `now`/`date`/`zone` handling (1A-4, 1A-7: auth, rate limits, `/docs` LAN gating per `PRD.md:154`)
- e2e ASGI tests and OpenAPI-served-behind-auth (1A-7 exit criteria)
- SQLite/JAKIM/calc adapters, views/templates/themes (1A-5/1A-6, 1B), service/installer (1A-8), version bump past `0.1.0` and `/api/v2` machinery.
