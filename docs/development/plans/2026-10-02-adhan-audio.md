# Adhan audio upload + ADHAN-state playback — Implementation Plan (issue #38)

> **For workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: plan self-review below, `oracle` on the branch diff before PR handoff. Do NOT commit this plan file until the slice is approved for implementation.

**Goal:** An admin can upload an adhan MP3, set master volume + quiet hours + per-prayer enable, and the public display plays it during the ADHAN overlay (silent otherwise), additive-only with existing installs silent by default.

**Architecture:** New `adapters/adhan_audio.py` store (MP3 magic check, 10MB cap, canonical `adhan.mp3` in `media_dir`, no new dependencies); `Settings` + `SettingsDTO` gain `adhan_audio_enabled`/`adhan_volume`/`quiet_hours_start`/`quiet_hours_end`/`adhan_muted_prayers` with VO guards; `POST`/`DELETE /api/adhan-audio` (admin, base64 JSON like playlist uploads); display route computes the effective audio URL server-side (enabled + file exists + prayer not muted + not quiet hours) and `build_display_context` carries `adhan_audio_url`/`adhan_volume`; `display.html` ADHAN overlay renders `<audio autoplay>` and `app.js` sets volume + plays; admin settings UI gains an Audio section; fixtures + contract + changelog ship the additive change.

**Tech Stack:** Python 3.11+ (stdlib MP3 magic check, no new deps), FastAPI/Pydantic, SQLite kv settings, Jinja + vanilla JS (`admin.js`/`app.js`), pytest markers `unit/contract/e2e`.

**References:** Issue #38; PRD FR-2.2 (line 90: overlay + optional local audio, admin-uploaded chime/MP3 only, volume schedule + mute) + NFR-5.3 line 212 (per-prayer enable + master volume + quiet hours); `src/muhideen/adapters/images.py:16,44-78` (store pattern: caps, `ValueError`, safe names); `src/muhideen/api/app.py:1136-1192` (playlist base64 upload pattern), `app.py:822-831` (PUT settings), `app.py:624-752` (display route + ctx), `app.py:461-465` (`media_dir` default `static/uploads`, served by the `/static` mount at `app.py:521`); `src/muhideen/core/values.py:289-312` (`Settings`), `src/muhideen/api/dto.py:362-442` (`SettingsDTO`); `src/muhideen/adapters/sqlite_repo.py:228-304` (kv load/save); `src/muhideen/views/display.py:81-201` (builder); `src/muhideen/views/templates/display.html:10-16` (ADHAN overlay); `src/muhideen/static/app.js:1-166` (reload-on-state model — ADHAN overlay is server-rendered per state, so playback ties to the ADHAN window with no client state machine); `src/muhideen/views/templates/admin/settings.html:73-156` (Time section), `src/muhideen/static/admin.js:312-323` (save body); `api/fixtures/playlist-image-upload.json` (upload fixture shape); `docs/api-contract.md` (settings + playlist sections); NFR-5.5 (no new Node/toolchain, fixtures + changelog with every API change).

**Branch:** `feature/adhan-audio` (cut when approved; base on `main` — independent of other open issues)

---

## Background the implementer needs

### Field naming + values (locked)

- `adhan_audio_enabled: bool = False` — master opt-in; existing installs silent by default.
- `adhan_volume: int = 70` — range 0–100; client sets `audio.volume = v/100`.
- `quiet_hours_start: str | None = None`, `quiet_hours_end: str | None = None` — `"HH:MM"` each; both-or-neither (one set without the other is `ValueError`); overnight wrap allowed (`22:00`–`06:00` means quiet when `now >= start or now < end`, else `start <= now < end` on `"HH:MM"` string compare).
- `adhan_muted_prayers: list[str] = []` — prayer literals (`fajr/dhuhr/asr/maghrib/isha/jumuah`); each entry must be a Prayer Time Marker (reuse the `MarkerName` + `marker_kind` boundary-reject pattern from `countdown_before_adhan_overrides`, `values.py:335-350`).
- DTO mirrors 1:1 with additive defaults (same precedent as `timezone`/`asr_juristic`); kv keys `adhan_audio_enabled` (`0`/`1` via `_parse_bool`), `adhan_volume`, `quiet_hours_start`, `quiet_hours_end`, `adhan_muted_prayers` (comma-joined, sorted — mirror `countdown_min_*` row style).

### Design decisions (locked)

1. MP3-only, canonical name: `store_adhan_audio(data, dest_dir)` accepts MP3 magic only (`ID3` prefix or MPEG frame sync `0xFF 0xE_` on first two bytes), 10MB cap, writes `adhan.mp3` (replaced on upload). No re-encode (audio has no Pillow equivalent; byte-store after validation). No duration check (no new deps per NFR-5.5 — size cap is the bound). `delete_adhan_audio(dest_dir)` removes it (`missing_ok`).
2. No new endpoint for reads: the display gets the URL through the server-rendered context, not an API. New routes: `POST /api/adhan-audio` (admin, `{audio_base64}` → 201 `{file, size}`; 400 invalid base64/non-MP3, 413 over cap) and `DELETE /api/adhan-audio` (admin → `{ok}`).
3. Effective-audio rule lives in the display route (not the builder, not JS): URL present iff `enabled and file exists and next_key not in muted and not in_quiet_hours(now)`. Builder takes `adhan_audio_url: str | None` + `adhan_volume: int` params and sets ctx keys (default `None`/`70` so existing builder callers/tests don't churn). Quiet evaluation uses the clock `now` in the device tz (already on the event).
4. Client playback is dumb: overlay contains `<audio id="adhan-audio" src="..." preload="auto">`; `app.js` on load: if element present → `volume = {{volume}}/100`, `play().catch(()=>{})` (kiosk Chromium runs with no-gesture autoplay; document in contract). No loop (ADHAN overlay duration bounds it; `adhan_duration_s` unchanged). State reloads re-render the overlay, so no client state tracking.
5. Uploaded file served via the existing `/static` mount (`media_dir` default `static/uploads` → URL `/static/uploads/adhan.mp3`), same assumption as playlist images. Custom `media_dir` deployments keep working iff the dir is under the static root (document in contract).
6. Non-goals: bundled recitation (licensing — PRD explicit), per-display audio, streaming/upload progress, format conversion, scheduler-driven playback, volume schedule beyond quiet hours, `locales` changes.

## File Structure

- Create: `src/muhideen/adapters/adhan_audio.py` — magic check + store/delete + caps
- Test: `tests/unit/test_adhan_audio.py` — type/size/round-trip/delete (minimal valid MP3 bytes = `ID3` header + padding; invalid = PNG bytes or text)
- Modify: `src/muhideen/core/values.py` — 5 fields + guards (volume range, quiet both-or-neither + `HH:MM` shape, muted prayer-only)
- Modify: `src/muhideen/api/dto.py` — 5 DTO fields + mappers
- Modify: `src/muhideen/adapters/sqlite_repo.py` — load fallbacks + save keys
- Modify: `src/muhideen/api/app.py` — POST/DELETE routes + display-route effective rule
- Modify: `src/muhideen/views/display.py` — builder params + ctx keys
- Modify: `src/muhideen/views/templates/display.html` — ADHAN overlay `<audio>` (both ADHAN render sites: overlay `overlay-adhan` at `:10-16`; check if a second ADHAN h1 exists for dim/other states — wire every site that renders during `ADHAN` state, none other)
- Modify: `src/muhideen/static/app.js` — volume + play on load
- Modify: `src/muhideen/views/templates/admin/settings.html` — Audio section (enable toggle, upload input, volume, quiet start/end, per-prayer checkboxes)
- Modify: `src/muhideen/static/admin.js` — upload base64 PUT/POST, save-body fields, DEFAULTS cover DTO (guard test `test_admin_js_defaults_cover_settings_dto` must stay green)
- Fixtures: `api/fixtures/adhan-audio-upload.json` (request `{audio_base64}` + response `{file, size}` samples) + `api/fixtures/settings.json` gains the 5 keys
- Docs: `docs/api-contract.md` (new audio section + settings fields), `CHANGELOG.md` Added entry
- Test: settings API round-trip + 422s, display e2e (audio present/absent matrix), contract parity

No migration (kv fallbacks), no locale change, no `setup.html` change (timezone-style first-boot field NOT needed — audio defaults silent; admin uploads post-setup).

---

### Task 1: Audio store adapter + unit tests

**Files:** Create `src/muhideen/adapters/adhan_audio.py`; create `tests/unit/test_adhan_audio.py`

**Goal:** Validated MP3 byte-store with caps, no new dependencies.

- [ ] **Step 1: Write the failing tests**

```python
"""Adhan audio store: MP3-only, 10MB cap, canonical name."""

import pytest

pytestmark = pytest.mark.unit

VALID_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 1024  # minimal ID3 header
FRAME_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 1024  # MPEG frame sync

def test_store_accepts_id3_and_frame_sync(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import store_adhan_audio

    for blob in (VALID_MP3, FRAME_MP3):
        path = store_adhan_audio(blob, tmp_path)
        assert path.name == "adhan.mp3" and path.read_bytes() == blob

def test_store_rejects_non_mp3_and_oversize(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import MAX_ADHAN_BYTES, store_adhan_audio

    with pytest.raises(ValueError, match="unsupported audio"):
        store_adhan_audio(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, tmp_path)
    with pytest.raises(ValueError, match="exceeds"):
        store_adhan_audio(b"ID3" + b"\x00" * (MAX_ADHAN_BYTES + 1), tmp_path)

def test_delete_is_missing_ok(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import delete_adhan_audio, store_adhan_audio

    store_adhan_audio(VALID_MP3, tmp_path)
    assert delete_adhan_audio(tmp_path) is True
    assert delete_adhan_audio(tmp_path) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_adhan_audio.py -q`
Expected: FAIL (`ModuleNotFoundError: muhideen.adapters.adhan_audio`).

- [ ] **Step 3: Minimal implementation** (mirror `images.py` structure/naming):

```python
"""Adhan audio store: MP3-only byte store with caps (no re-encode)."""

from __future__ import annotations

from pathlib import Path

MAX_ADHAN_BYTES = 10 * 1024 * 1024
"""Upload cap: inputs larger than 10MB are rejected before sniffing."""

ADHAN_FILENAME = "adhan.mp3"
"""Canonical stored name: uploads replace each other; the display URL is stable."""


def _is_mp3(data: bytes) -> bool:
    """Sniff MP3 magic: ID3v2 header or MPEG frame sync (no new deps)."""
    if len(data) < 4:
        return False
    if data[:3] == b"ID3":
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def store_adhan_audio(data: bytes, dest_dir: str | Path) -> Path:
    """Validate + store ``data`` as adhan.mp3; return its path.

    Raises ``ValueError`` when over 10MB or not MP3 magic.
    """
    if len(data) > MAX_ADHAN_BYTES:
        raise ValueError(f"adhan audio exceeds 10MB limit: {len(data)} bytes")
    if not _is_mp3(data):
        raise ValueError("unsupported audio format: expected MP3")
    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ADHAN_FILENAME
    path.write_bytes(data)
    return path


def delete_adhan_audio(dest_dir: str | Path) -> bool:
    """Remove adhan.mp3; return True when a file was removed."""
    path = Path(dest_dir) / ADHAN_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_adhan_audio.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/muhideen/adapters/adhan_audio.py tests/unit/test_adhan_audio.py && uv run ruff format --check src/muhideen/adapters/adhan_audio.py tests/unit/test_adhan_audio.py`

### Task 2: Settings VO + DTO + repo + fixtures/contract-fields

**Files:** `src/muhideen/core/values.py`, `src/muhideen/api/dto.py`, `src/muhideen/adapters/sqlite_repo.py`, `api/fixtures/settings.json`, `docs/api-contract.md` (settings sections only)

**Goal:** The five fields exist, validate, persist, and survive the wire with KL-style legacy defaults (silent by default).

- [ ] **Step 1: Write the failing tests**

Settings-guard file (`tests/unit/test_core_values.py`): add

```python
def test_adhan_audio_defaults_silent_and_guards() -> None:
    from muhideen.core.values import Settings

    s = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert (s.adhan_audio_enabled, s.adhan_volume) is not None  # placeholder shape check
```

then flesh to real asserts (enabled False, volume 70, quiet None/None, muted []); plus `pytest.raises(ValueError)` for volume 101, quiet-start-without-end, muted `["syuruq"]` (boundary), muted `["nope"]` (unknown). Repo file (`tests/integration/test_sqlite_settings_repo.py`): round-trip (enabled True, volume 40, quiet 22:00–06:00, muted ["fajr"]) + legacy fallback (delete the 5 keys → defaults).

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/unit/test_core_values.py -q -k adhan && uv run pytest tests/integration/test_sqlite_settings_repo.py -q -k adhan`
Expected: FAIL (`TypeError: unexpected keyword` / `assert` mismatch).

- [ ] **Step 3: Minimal implementation**

```python
# values.py Settings (after timezone field):
adhan_audio_enabled: bool = False
adhan_volume: int = 70
quiet_hours_start: str | None = None
quiet_hours_end: str | None = None
adhan_muted_prayers: list[str] = field(default_factory=list, hash=False)  # check Settings dataclass field style for list defaults (countdown_before_adhan_overrides uses field(default_factory=dict, hash=False) — mirror it)
# __post_init__:
if not 0 <= self.adhan_volume <= 100:
    raise ValueError(f"adhan_volume out of range 0-100: {self.adhan_volume}")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # module-level; check file imports re first
if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
    raise ValueError("quiet hours need both start and end")
for bound in (self.quiet_hours_start, self.quiet_hours_end):
    if bound is not None and not _HHMM.match(bound):
        raise ValueError(f"quiet hours must be HH:MM: {bound!r}")
for prayer_key in self.adhan_muted_prayers:
    try: marker = MarkerName(prayer_key)
    except ValueError: raise ValueError(f"unknown prayer for adhan mute: {prayer_key!r}") from None
    if marker_kind(marker) is MarkerKind.BOUNDARY:
        raise ValueError(f"boundary marker cannot mute adhan: {prayer_key}")
```

DTO: 5 fields with same defaults + 1:1 mappers; `adhan_muted_prayers: list[str]` (validate literals? VO owns validation — DTO passes through like countdown overrides do; check how DTO declares that dict). Repo load: `adhan_audio_enabled=_parse_bool(kv.get("adhan_audio_enabled", "0"))`, `adhan_volume=int(kv.get("adhan_volume", "70"))`, `quiet_hours_start=kv.get("quiet_hours_start")`, `quiet_hours_end=kv.get("quiet_hours_end")`, `adhan_muted_prayers=sorted(p.split(",")...)` — store as single comma-joined key `adhan_muted_prayers` (sorted, empty string when none; load splits + drops empties). Save mirrors. Fixture `settings.json`: add the 5 keys. Contract settings sections: document fields + quiet wrap semantics + silent-by-default.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_core_values.py tests/integration/test_sqlite_settings_repo.py -q` then contract tests (`uv run pytest tests/contract -q`)
Expected: PASS.

- [ ] **Step 5: Lint touched py files** (`ruff check` + `format --check`).

### Task 3: Upload/delete API + contract section + fixtures

**Files:** `src/muhideen/api/app.py`, `api/fixtures/adhan-audio-upload.json` (create), `docs/api-contract.md` (new section)

**Goal:** Admin upload/delete with the playlist-upload status-code contract.

- [ ] **Step 1: Write the failing API tests**

Find the playlist-upload test file (`grep -rln "playlist.*items\|image_base64\|invalid base64" tests/`). In the same style add (admin-authed client + `media_dir` tmp via AppDeps — read the file's fixtures first):

```python
def test_adhan_upload_round_trip(admin_client_with_media_tmp) -> None:
    # POST /api/adhan-audio {audio_base64: b64(ID3 blob)} → 201 {file: "adhan.mp3", size}
    # file exists on disk under media dir
def test_adhan_upload_rejects_non_mp3_and_oversize(...) -> None:
    # text bytes → 400; >10MB → 413
def test_adhan_delete(...) -> None:
    # DELETE after upload → {ok: true}; DELETE with no file → {ok: true} (idempotent)
def test_adhan_routes_require_admin(anon_client) -> None:
    # POST + DELETE → 401
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest <upload-test-file> -q -k adhan`
Expected: FAIL (404 no route).

- [ ] **Step 3: Minimal implementation** (mirror `upload_playlist_item`, `app.py:1136-1192`):

```python
class AdhanAudioUploadDTO(ContractDTO):
    """Adhan MP3 upload: base64 bytes (no multipart parser on the offline footprint)."""
    audio_base64: Annotated[str, Field(min_length=1)]

@app.post("/api/adhan-audio", dependencies=[Depends(admin)], status_code=201)
def upload_adhan_audio(payload: AdhanAudioUploadDTO) -> dict[str, Any]:
    try: data = base64.b64decode(payload.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc: raise HTTPException(400, "invalid base64") from exc
    if len(data) > MAX_ADHAN_BYTES: raise HTTPException(413, "audio exceeds 10MB limit")
    try: stored = store_adhan_audio(data, media_dir)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    return {"file": stored.name, "size": len(data)}

@app.delete("/api/adhan-audio", dependencies=[Depends(admin)])
def delete_adhan_audio_route() -> dict[str, Any]:
    delete_adhan_audio(media_dir)
    return {"ok": True}
```

Name the handler distinctly from the adapter import (`delete_adhan_audio_route` or `import ... as`). Fixture `adhan-audio-upload.json`: `{request: {audio_base64: "<ID3 sample b64>"}, response: {file, size}}` mirroring `playlist-image-upload.json` shape. Contract: new `POST/DELETE /api/adhan-audio` section (MP3-only, 10MB, canonical name, served URL, status codes, kiosk autoplay note).

- [ ] **Step 4: Run tests** (`<upload-test-file>` + `tests/contract -q`)
Expected: PASS.

- [ ] **Step 5: Lint** (`ruff` on `app.py` + test file).

### Task 4: Display playback (builder + template + app.js + route rule)

**Files:** `src/muhideen/views/display.py`, `src/muhideen/views/templates/display.html`, `src/muhideen/static/app.js`, `src/muhideen/api/app.py` (display route), tests: `tests/unit/test_display_context.py`, `tests/e2e/test_display.py`

**Goal:** ADHAN overlay plays uploaded audio at the set volume unless muted/quiet/disabled; every other state silent.

- [ ] **Step 1: Write the failing tests**

Builder (`test_display_context.py`, existing `_dtos/_ctx` helpers): `test_context_carries_adhan_audio` — default ctx has `adhan_audio_url is None`; with explicit `adhan_audio_url="/static/uploads/adhan.mp3", adhan_volume=40` the ctx carries both. E2E (`test_display.py`, surface/client fixtures): matrix test `test_display_adhan_audio_matrix` — (a) fresh install (no file, defaults): no `id="adhan-audio"` in any state; (b) upload MP3 + enable + non-quiet: ADHAN-state HTML contains `<audio id="adhan-audio"` + `/static/uploads/adhan.mp3` + volume marker; (c) quiet hours covering now: no audio element; (d) muted `next_prayer`: no audio element. For (b)–(d) drive the display into ADHAN state via the existing walkthrough pattern (`test_state_walkthrough_adhan_overlay`: fetch prayer-day, advance clock to adhan+60s).

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/unit/test_display_context.py tests/e2e/test_display.py -q -k adhan`
Expected: FAIL (`TypeError` on builder kwargs / missing element).

- [ ] **Step 3: Minimal implementation**

```python
# display.py builder signature: adhan_audio_url: str | None = None, adhan_volume: int = 70
# return dict gains: "adhan_audio_url": adhan_audio_url, "adhan_volume": adhan_volume,
# route (display handler, after event resolved, before ctx build):
adhan_url = None
if settings.adhan_audio_enabled and event.next_key not in settings.adhan_muted_prayers... (next_key is str like "dhuhr"/"jumuah" — compare directly to muted list)
    and _in_quiet_hours(now_hhmm, settings.quiet_hours_start, settings.quiet_hours_end) is False
    and (media_dir / "adhan.mp3").exists():
    adhan_url = "/static/uploads/adhan.mp3"
# helper (module-level in app.py or domain? presentation rule → app.py private):
def _in_quiet_hours(now: str, start: str | None, end: str | None) -> bool:
    if start is None or end is None: return False
    return (start <= now < end) if start <= end else (now >= start or now < end)
# now_hhmm from the event now in device tz: event_dto.now.strftime("%H:%M")
```

URL constant: derive from `ADHAN_FILENAME` import (not a literal) — `/static/uploads/` prefix literal is fine (matches static mount; custom media_dir caveat already documented in Task 3 contract). Template ADHAN overlay (every site rendering during ADHAN state — currently `overlay-adhan` at `:10-16`; verify no second ADHAN site): `{% if adhan_audio_url %}<audio id="adhan-audio" src="{{ adhan_audio_url }}" preload="auto"></audio>{% endif %}` inside the overlay `<main>`. `app.js` (top-level, after dim block): `var adhanEl = document.getElementById("adhan-audio"); if (adhanEl) { adhanEl.volume = (window.__ADHAN_VOLUME__ || 70) / 100; ... }` — volume needs a DOM channel: render `data-volume="{{ adhan_volume }}"` on the audio tag and read it in JS (no globals): `adhanEl.volume = Math.min(1, Math.max(0, (parseInt(adhanEl.getAttribute("data-volume") || "70", 10)) / 100)); var p = adhanEl.play(); if (p && p.catch) p.catch(function () {});`

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_display_context.py tests/e2e/test_display.py -q`
Expected: PASS.

- [ ] **Step 5: Lint** (`ruff` py files; `node --check` app.js if node exists).

### Task 5: Admin UI (Audio section + admin.js)

**Files:** `src/muhideen/views/templates/admin/settings.html`, `src/muhideen/static/admin.js`

**Goal:** Admin can enable/upload/volume/quiet/mute from Settings; DTO-guard test stays green.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_admin_static.py::test_admin_js_defaults_cover_settings_dto` already enforces DEFAULTS ⊇ DTO — adding DTO fields in Task 2 turned it red until DEFAULTS updated. Add e2e settings-save coverage in `tests/e2e/test_admin_settings.py`: extend the full-body helper with the 5 audio keys (upload covered by Task 3 API tests, not here) — PUT with audio fields → 200 + GET pins them.

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/unit/test_admin_static.py -q`
Expected: FAIL (DTO has audio keys, DEFAULTS lacks them).

- [ ] **Step 3: Minimal implementation**

`settings.html` new Audio admin-section (after Time section): enable switch (`s-adhan-enabled`), file input (`s-adhan-file`) + upload/delete buttons (`s-adhan-upload`, `s-adhan-delete`) + status line, volume number 0–100 (`s-adhan-volume`), quiet start/end time inputs (`s-quiet-start`, `s-quiet-end`, `type="time"`), per-prayer checkboxes (`s-mute-fajr … s-mute-jumuah`, 6 incl. jumuah). `admin.js`: DEFAULTS += the 5 keys (`adhan_audio_enabled: false, adhan_volume: 70, quiet_hours_start: null, quiet_hours_end: null, adhan_muted_prayers: []` — match DTO defaults exactly); save body reads the controls (muted = checked boxes' prayer names; quiet empty string → null); upload handler: file → base64 → POST, status line update; delete handler: DELETE, status update. Prefill: settings page renders `current.*` values into the controls (mirror existing `value="{{ current.x }}"` / `checked` patterns).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_admin_static.py tests/e2e/test_admin_settings.py -q` then full `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Lint** (`node --check` admin.js; `ruff` test file).

### Task 6: Changelog + full gate

- [ ] **Step 1: CHANGELOG `[Unreleased] Added`**: one bullet — adhan audio (admin-uploaded MP3-only 10MB, master enable/volume, quiet hours incl. overnight wrap, per-prayer mute; ADHAN-overlay playback, restart NOT required for audio file swap but IS reflected on next ADHAN render; silent by default; fixtures + contract). No dev-plan filename reference.
- [ ] **Step 2: Gate**: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #38 + PRD FR-2.2/NFR-5.3 → Tasks 1–5 (store, settings, API, playback incl. quiet/mute matrix, admin UI); bundled recitation, per-display audio, conversion, volume-beyond-quiet, locales explicitly excluded.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task — files, tests, commands, exact code all named.
3. Type consistency: `bool/int/str|None/list[str]` mirrored VO↔DTO↔kv↔JSON↔JS; `adhan.mp3` single owner (`ADHAN_FILENAME`); volume `70` default in VO/DTO/builder/JS-fallback.
4. Momus dry gate: `images.py:16,44-78` / `app.py:1136-1192,822-831,624-752,521,461-465` / `values.py:289-312,335-350` / `dto.py:362-442` / `sqlite_repo.py:228-304` / `display.py:81-201` / `display.html:10-16` / `app.js:1-166` / `settings.html:73-156` / `admin.js:312-323` / `playlist-image-upload.json` verified on disk this session; order fixed (store → settings → API → playback → UI so each pin passes); each task red→green with exact `uv run` commands. `setup.html` untouched (audio defaults silent — nothing to set at first boot).
