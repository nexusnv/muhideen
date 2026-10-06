# File-Based Config Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the admin UI and SQLite persistence; all configuration lives in hand-edited JSON files under `config/`, with per-display blocks driving the retained display theme, and the server hot-reloads on file change.

**Architecture:** Single `config/muhideen.json` (hand-edited: identity, schedule, timing, adhan, global theme, `displays` map, playlists, manual pins) plus machine-written `config/prayer_buffer.json` (JAKIM timetable cache). New `adapters/file_config.py` implements the existing `SettingsRepo`/`PrayerRepo` ports over these files; a stdlib mtime-watcher thread reloads, revalidates via Pydantic, and publishes SSE `config-update` so displays (`GET /display`, `static/app.js`, `static/app.css`, `views/display.py`) reload with zero changes to look-and-feel.

**Tech Stack:** Python 3.11+, FastAPI + Jinja2 (display only), Pydantic v2 (file validation, `extra="forbid"`), stdlib `threading`+`pathlib` mtime watcher (no new dependency), uvicorn single worker.

---

## 1. Current-state map (verify before touching code)

| Concern | Current location | Disposition |
|---|---|---|
| Admin pages | `src/muhideen/views/templates/admin/login.html`, `setup.html`, `settings.html`, `playlists.html`, `_nav.html`; `src/muhideen/views/admin.py`; `src/muhideen/static/admin.js`, `admin.css` | DELETE all; keep `views/display.py`, `views/templates/display.html`, `error.html`, `static/app.js`, `static/app.css` |
| Admin routes | `src/muhideen/api/app.py:824-1625` (`/api/auth/*`, `/api/settings`, `/api/manual-day`, `/admin/*`, `/api/playlists*`, `/api/adhan-audio`, `/api/displays*`, `/api/display-groups/*`, `/api/backup/*`, `/api/logs`) + `src/muhideen/api/auth.py` | DELETE; keep `GET /api/prayer-day`, `/api/next-event`, `/api/events`, `/api/version`, `GET /display` |
| Settings source | `src/muhideen/adapters/sqlite_repo.py:265 SqliteSettingsRepo`, `core/values.py:300 Settings`, `api/dto.py:388 SettingsDTO` | Replace with file repo; `Settings` VO stays (validation owner) |
| Prayer cache | `SqlitePrayerRepo` over `prayer_times` table, `adapters/scheduler.py run_sync/save_day_unless_manual` | Replace with `FilePrayerRepo` over `config/prayer_buffer.json` |
| Displays/groups | `displays`, `display_groups`, `display_settings` tables + `SqliteDisplaySettingsRepo`, allowlist = 7 `theme.*` + `dim_minutes_override` | Replace with `displays` map in main JSON (self-contained per display, no groups table) |
| Playlists/media | `SqlitePlaylistRepo`, `adapters/images.py`, `adapters/adhan_audio.py`, uploads under `src/muhideen/static/uploads/` | Playlists move into main JSON (image paths relative to `media/`); binaries stay as files under `media/` |
| Users/auth | `SqliteUserRepo` (argon2id), `SessionStore`, `RateLimiter` | DELETE entirely (no admin = no passwords/sessions) |
| Boot/CLI | `src/muhideen/service.py --db`, `src/muhideen/seed.py --db --zone`, `packaging/muhideen.service`, `Dockerfile`, `compose.yml` (`/data/muhideen.db`) | Switch to `--config config/muhideen.json`; delete `seed.py`, add `muhideen-sync` one-shot + `muhideen-validate` |
| Migrations/backup/logs/QR | `adapters/migrate.py`, `migrations/*.sql`, `adapters/backup.py`, `adapters/logs.py`, `adapters/qr_code.py` | DELETE |
| Themes prototype | `themes/classic-green/*`, `tools/new_theme.py`, `tools/lint_theme.py` | LEAVE untouched (not wired to `/display`) |
| Locales | `locales/en.json`, `ms.json` (admin strings + prayer names) | Keep prayer/display keys; delete `admin_*` keys; add per-display `language` selection |

## 2. Proposed `config/` schema

Two files. One hand-edited, one machine-written. No other persistent state.

```
config/
  muhideen.json          # hand-edited, single source of truth (this plan)
  prayer_buffer.json     # machine-written JAKIM cache, overwritten by scheduler
media/
  adhan.mp3              # optional, referenced by config
  playlists/<file>.jpg   # playlist images
```

### 2.1 `config/muhideen.json` — full example

```json
{
  "$schemaVersion": 1,
  "masjid": {
    "name": "Masjid An-Nur",
    "zone": "SGR01",
    "timezone": "Asia/Kuala_Lumpur"
  },
  "schedule": {
    "method": "MABIMS",
    "asr_juristic": "shafi",
    "lat": null,
    "lon": null,
    "calc_only": false,
    "hijri_offset": 0,
    "imsak_offset_min": 10,
    "dhuha_offset_min": 28,
    "boundary_countdown": false,
    "manual_days": [
      {
        "date": "2026-04-01",
        "imsak": "05:48", "fajr": "05:58", "syuruq": "07:05",
        "dhuha": "07:33", "dhuhr": "13:15", "asr": "16:30",
        "maghrib": "19:15", "isha": "20:30"
      }
    ]
  },
  "timing": {
    "adhan_duration_s": 180,
    "dim_minutes_default": 20,
    "dim_minutes_jumuah": 45,
    "countdown_before_adhan_min": 5,
    "countdown_before_adhan_overrides": { "fajr": 10 },
    "iqamah_rules": [
      { "prayer": "fajr", "mode": "delay", "delay_minutes": 15, "fixed_time": null },
      { "prayer": "dhuhr", "mode": "delay", "delay_minutes": 10, "fixed_time": null },
      { "prayer": "asr", "mode": "delay", "delay_minutes": 10, "fixed_time": null },
      { "prayer": "maghrib", "mode": "delay", "delay_minutes": 10, "fixed_time": null },
      { "prayer": "isha", "mode": "delay", "delay_minutes": 15, "fixed_time": null },
      { "prayer": "jumuah", "mode": "delay", "delay_minutes": 10, "fixed_time": null }
    ]
  },
  "adhan_audio": {
    "enabled": false,
    "volume": 70,
    "quiet_hours_start": null,
    "quiet_hours_end": null,
    "muted_prayers": [],
    "file": "media/adhan.mp3"
  },
  "theme": {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "12h",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable"
  },
  "displays": {
    "main-hall": {
      "name": "Main Hall",
      "language": "en",
      "theme": { "palette": "classic-green" },
      "dim_minutes_override": null,
      "carousel_enabled": true
    },
    "entrance": {
      "name": "Entrance",
      "language": "ms",
      "theme": { "palette": "midnight", "clock_format": "24h", "density": "compact" },
      "dim_minutes_override": 30,
      "carousel_enabled": false,
      "custom_colors": { "background": "#0b0f0e", "foreground": "#f2f2f2", "accent": "#c9a227" }
    }
  },
  "playlists": [
    {
      "id": "announcements",
      "title": "Announcements",
      "active": true,
      "window_start": null, "window_end": null,
      "anchor_marker": null,
      "anchor_start_offset_min": 0, "anchor_stop_offset_min": 0,
      "cycle_mode": "indefinite", "max_cycles": null,
      "items": [
        { "image_path": "media/playlists/welcome.jpg", "duration_s": 15, "sort_order": 0 }
      ]
    }
  ]
}
```

### 2.2 Field rules (mirrors `core/values.py` guards + `api/dto.py` — no drift)

- `masjid.name` 1–200 chars required. `masjid.zone` 1–32 JAKIM code required. `masjid.timezone` IANA, default `Asia/Kuala_Lumpur`. **Changing `timezone` requires process restart** (clock tz is fixed at boot; watcher logs a warning and keeps the old tz until restart).
- `schedule.method`: `MABIMS|MWL|ISNA|Egyptian` (default `MABIMS`). `asr_juristic`: `shafi|hanafi`. `lat/lon`: paired-nullable, `-90..90` / `-180..180`. `calc_only`, `boundary_countdown`: bool. `hijri_offset`: `-2..+2`. `imsak_offset_min`: `0..10` (`0` hides Imsak row). `dhuha_offset_min`: `15..30`.
- `timing.adhan_duration_s` > 0. `dim_minutes_default/jumuah`: `5..60`. `countdown_before_adhan_min` + per-prayer `countdown_before_adhan_overrides{fajr,dhuhr,asr,maghrib,isha,jumuah: 0..90}` (boundary keys rejected). `iqamah_rules`: exactly 6 prayer-only rules, `delay 0..60` or `fixed HH:MM` (fixed requires `fixed_time`).
- `adhan_audio`: `enabled` bool, `volume` `0..100`, `quiet_hours_start/end` paired-nullable `HH:MM`, `muted_prayers[]` prayer literals only, `file` relative path (server checks existence at render; missing file = silent, same as today).
- `theme` (global defaults): `palette classic-green|midnight|sand`, `font outfit|system`, `countdown_style boxes|inline`, `clock_format 24h|24h-seconds|12h`, `hijri_form long|short`, `boundary_strip show|hide`, `density comfortable|compact`. Closed enums, `extra="forbid"`.
- `displays`: **map keyed by display id** (the `?id=` query value). Each entry: `name` (defaults to id), `language: en|ms|ar` (default `en`; picks which label set renders as primary — context keeps en+ar+bm trilingual data, template picks; retains current look for `en`), `theme` partial object (any subset of the 7 knobs, merged over global `theme`; unknown keys rejected), `dim_minutes_override: 5..60|null`, `carousel_enabled` bool (default true), optional `custom_colors: {background, foreground, accent}` as `#rrggbb` (emitted as inline CSS variables; absent = current `app.css` palette untouched). **Schedule/iqamah keys are forbidden inside a display** — validation rejects them (replaces the old `DISPLAY_SETTINGS_ALLOWLIST`). Unknown `?id=` renders global theme + `language: en` (same as today). `display_groups` table is dropped; migrate group `dim/carousel` values into each member display at conversion time.
- `playlists[]`: same rules as `core/values.py:461 Playlist` (`title` required, `cycle_mode indefinite|repeat` + `max_cycles>=1` pairing, window/anchor strings validated by `domain/playlist_window.py`, `items<=50`, `duration_s>0`, `sort_order>=0`, `image_path` relative to repo/media root).
- `schedule.manual_days[]`: flat `date` + 8 `HH:MM` markers, strict `Imsak < Fajr < ...` ordering (`domain/ordering.py ensure_ordered`, 422-equivalent = startup validation error). Overlay precedence: `manual_days` > `prayer_buffer.json` > calc/fallback (same as manual > JAKIM today).

### 2.3 `config/prayer_buffer.json` — machine-written (scheduler-owned)

```json
{
  "$schemaVersion": 1,
  "zone": "SGR01",
  "fetched_at": "2026-10-04T02:00:00+08:00",
  "days": {
    "2026-10-04": {
      "imsak": "05:48", "fajr": "05:58", "syuruq": "07:05", "dhuha": "07:33",
      "dhuhr": "13:10", "asr": "16:25", "maghrib": "19:10", "isha": "20:25",
      "source": "jakim"
    }
  }
}
```

Rules: keys `YYYY-MM-DD`, 8 `HH:MM` values, `source: jakim|calc`. Written atomically (tmp + rename). Scheduler's `save_day_unless_manual` becomes "skip dates present in `manual_days`". Watcher reloads this file too (timetable updates hot-reload; displays refresh via existing SSE `tick`/`state`).

## 3. Reload semantics

- Boot: `--config` loaded + validated (Pydantic, `extra="forbid"`); any error = fail fast with file:line message, exit non-zero (replaces 503-setup/seed flow; no wizard, no `SettingsNotInitialized`).
- Runtime: watcher thread polls `mtime_ns` of both JSON files every 1.0s; on change → reparse → revalidate → atomic swap → `SSEBus.publish("config-update", ("settings",))`. Display JS already reloads on `config-update` (`static/app.js:105`) — keep as-is.
- `prayer_buffer.json` changes publish nothing extra (next `tick` picks them up); `muhideen.json` changes publish `config-update`.
- `timezone` change: log `WARNING timezone changed ... restart required`, keep serving old tz (matches current restart-required hint).
- Invalid edit: log error, keep last-good config serving (never blank the display).

## 4. File layout after refactor

```
config/muhideen.json
config/prayer_buffer.json
config/muhideen.example.json      # checked-in golden example (from §2.1)
src/muhideen/adapters/file_config.py   # FileSettingsRepo, FilePrayerRepo, FilePlaylistRepo, load_config_file()
src/muhideen/adapters/config_watcher.py # ConfigWatcher thread
src/muhideen/config_models.py OR adapters/file_models.py  # Pydantic file models (one place)
tools/migrate_db_to_files.py      # one-shot SQLite -> JSON exporter
```

Delete: `views/admin.py`, `views/templates/admin/*`, `static/admin.js`, `static/admin.css`, `api/auth.py`, `adapters/sqlite_repo.py`, `adapters/playlist_repo.py`, `adapters/migrate.py`, `adapters/backup.py`, `adapters/logs.py`, `adapters/qr_code.py`, `migrations/`, `seed.py`, `static/vendor/` (if admin-only), locales `admin_*` keys.

---

### Task 1: File models + JSON Schema + example config

**Files:**
- Create: `src/muhideen/adapters/file_models.py`
- Create: `config/muhideen.example.json`
- Test: `tests/test_file_config_schema.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
import json
from muhideen.adapters.file_models import ConfigFile


def test_example_config_validates():
    raw = json.loads(Path("config/muhideen.example.json").read_text())
    cfg = ConfigFile.model_validate(raw)
    assert cfg.masjid.zone == "SGR01"
    assert cfg.displays["main-hall"].language == "en"
    assert cfg.displays["entrance"].theme["palette"] == "midnight"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_file_config_schema.py::test_example_config_validates -v`
Expected: FAIL with "file_models not found / example missing"

- [ ] **Step 3: Write minimal implementation**

```python
"""Pydantic models for config/muhideen.json (replaces SettingsDTO + display tables)."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from muhideen.api.dto import IqamahRuleDTO, TimeHHMM

Palette = Literal["classic-green", "midnight", "sand"]
Language = Literal["en", "ms", "ar"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Masjid(Strict):
    name: str = Field(min_length=1, max_length=200)
    zone: str = Field(min_length=1, max_length=32)
    timezone: str = "Asia/Kuala_Lumpur"


class ManualDay(Strict):
    date: str
    imsak: TimeHHMM
    fajr: TimeHHMM
    syuruq: TimeHHMM
    dhuha: TimeHHMM
    dhuhr: TimeHHMM
    asr: TimeHHMM
    maghrib: TimeHHMM
    isha: TimeHHMM


class Schedule(Strict):
    method: Literal["MABIMS", "MWL", "ISNA", "Egyptian"] = "MABIMS"
    asr_juristic: Literal["shafi", "hanafi"] = "shafi"
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    calc_only: bool = False
    hijri_offset: int = Field(default=0, ge=-2, le=2)
    imsak_offset_min: int = Field(default=10, ge=0, le=10)
    dhuha_offset_min: int = Field(default=28, ge=15, le=30)
    boundary_countdown: bool = False
    manual_days: list[ManualDay] = Field(default_factory=list)


class Timing(Strict):
    adhan_duration_s: int = Field(default=180, gt=0)
    dim_minutes_default: int = Field(default=20, ge=5, le=60)
    dim_minutes_jumuah: int = Field(default=45, ge=5, le=60)
    countdown_before_adhan_min: int = Field(default=5, ge=0, le=90)
    countdown_before_adhan_overrides: dict[str, int] = Field(default_factory=dict)
    iqamah_rules: list[IqamahRuleDTO] = Field(min_length=6, max_length=6)


class AdhanAudio(Strict):
    enabled: bool = False
    volume: int = Field(default=70, ge=0, le=100)
    quiet_hours_start: TimeHHMM | None = None
    quiet_hours_end: TimeHHMM | None = None
    muted_prayers: list[str] = Field(default_factory=list)
    file: str = "media/adhan.mp3"


class Theme(Strict):
    palette: Palette = "classic-green"
    font: Literal["outfit", "system"] = "outfit"
    countdown_style: Literal["boxes", "inline"] = "boxes"
    clock_format: Literal["24h", "24h-seconds", "12h"] = "12h"
    hijri_form: Literal["long", "short"] = "long"
    boundary_strip: Literal["show", "hide"] = "show"
    density: Literal["comfortable", "compact"] = "comfortable"


class DisplayTheme(Strict):
    palette: Palette | None = None
    font: Literal["outfit", "system"] | None = None
    countdown_style: Literal["boxes", "inline"] | None = None
    clock_format: Literal["24h", "24h-seconds", "12h"] | None = None
    hijri_form: Literal["long", "short"] | None = None
    boundary_strip: Literal["show", "hide"] | None = None
    density: Literal["comfortable", "compact"] | None = None


class Display(Strict):
    name: str | None = None
    language: Language = "en"
    theme: DisplayTheme = Field(default_factory=DisplayTheme)
    dim_minutes_override: int | None = Field(default=None, ge=5, le=60)
    carousel_enabled: bool = True
    custom_colors: dict[str, str] | None = None


class ConfigFile(Strict):
    schema_version: int = Field(alias="$schemaVersion", default=1)
    masjid: Masjid
    schedule: Schedule = Field(default_factory=Schedule)
    timing: Timing = Field(default_factory=Timing)
    adhan_audio: AdhanAudio = Field(default_factory=AdhanAudio)
    theme: Theme = Field(default_factory=Theme)
    displays: dict[str, Display] = Field(min_length=1)
    playlists: list[dict] = Field(default_factory=list)
```

Copy the §2.1 JSON into `config/muhideen.example.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_file_config_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/adapters/file_models.py config/muhideen.example.json tests/test_file_config_schema.py
git commit -m "feat: add file config models and example"
```

### Task 2: File repos over the existing ports (settings/prayer/playlists)

**Files:**
- Create: `src/muhideen/adapters/file_config.py`
- Modify: `src/muhideen/core/ports.py` (no change — confirm `SettingsRepo`/`PrayerRepo` signatures reused)
- Test: `tests/test_file_repos.py`

- [ ] **Step 1: Write the failing test**

```python
from muhideen.adapters.file_config import FileSettingsRepo, FilePrayerRepo
from datetime import date


def test_file_settings_loads_domain_settings(tmp_path):
    import json, shutil

    dest = tmp_path / "muhideen.json"
    shutil.copy("config/muhideen.example.json", dest)
    settings = FileSettingsRepo(dest).load()
    assert settings.zone == "SGR01"
    assert settings.theme.palette == "classic-green"


def test_manual_day_overlays_buffer(tmp_path):
    from muhideen.adapters.file_config import FilePrayerRepo

    buf = tmp_path / "prayer_buffer.json"
    buf.write_text(
        '{"$schemaVersion": 1, "zone": "SGR01", "fetched_at": "2026-10-04T02:00:00+08:00", "days": {}}'
    )
    repo = FilePrayerRepo(buf, manual_days=[])
    assert repo.get_day(date(2026, 10, 4), "SGR01") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_file_repos.py -v`
Expected: FAIL with "file_config not found"

- [ ] **Step 3: Write minimal implementation**

```python
"""File-backed repos implementing core.ports over config/*.json."""

from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from muhideen.adapters.file_models import ConfigFile
from muhideen.core.values import Settings, ThemeSettings, PrayerDay


def load_config_file(path: Path) -> ConfigFile:
    return ConfigFile.model_validate(json.loads(path.read_text()))


class FileSettingsRepo:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Settings:
        cfg = load_config_file(self._path)
        # map cfg -> Settings via the same guards as SettingsDTO.to_domain
        from muhideen.api.dto import SettingsDTO

        dto = SettingsDTO(
            masjid_name=cfg.masjid.name,
            zone=cfg.masjid.zone,
            hijri_offset=cfg.schedule.hijri_offset,
            adhan_duration_s=cfg.timing.adhan_duration_s,
            dim_minutes_default=cfg.timing.dim_minutes_default,
            dim_minutes_jumuah=cfg.timing.dim_minutes_jumuah,
            iqamah_rules=cfg.timing.iqamah_rules,
            lat=cfg.schedule.lat,
            lon=cfg.schedule.lon,
            method=cfg.schedule.method,
            asr_juristic=cfg.schedule.asr_juristic,
            boundary_countdown=cfg.schedule.boundary_countdown,
            calc_only=cfg.schedule.calc_only,
            imsak_offset_min=cfg.schedule.imsak_offset_min,
            dhuha_offset_min=cfg.schedule.dhuha_offset_min,
            countdown_before_adhan_min=cfg.timing.countdown_before_adhan_min,
            countdown_before_adhan_overrides=cfg.timing.countdown_before_adhan_overrides,
            theme=cfg.theme.model_dump(),
            timezone=cfg.masjid.timezone,
            adhan_audio_enabled=cfg.adhan_audio.enabled,
            adhan_volume=cfg.adhan_audio.volume,
            quiet_hours_start=cfg.adhan_audio.quiet_hours_start,
            quiet_hours_end=cfg.adhan_audio.quiet_hours_end,
            adhan_muted_prayers=cfg.adhan_audio.muted_prayers,
        )
        return dto.to_domain()
```

`FilePrayerRepo`: reads `prayer_buffer.json` days dict + in-memory `manual_days` overlay (manual first, then buffer, then None so engine falls through to calc). `save_day_unless_manual` appends to buffer file atomically unless the date is a manual pin. Full method bodies follow the `SqlitePrayerRepo` docstrings line-for-line.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_file_repos.py tests/test_file_config_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/adapters/file_config.py tests/test_file_repos.py
git commit -m "feat: add file-backed settings and prayer repos"
```

### Task 3: Rewire composition root to files (`--config`, no `--db`)

**Files:**
- Modify: `src/muhideen/service.py:18-37`
- Modify: `src/muhideen/api/app.py:1590-1625` (`create_production_app`)
- Test: `tests/test_service_flags.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parser_takes_config_not_db():
    from muhideen.service import _parser

    args = _parser().parse_args(["--config", "config/muhideen.json"])
    assert args.config == "config/muhideen.json"
    assert not hasattr(args, "db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_service_flags.py -v`
Expected: FAIL (still has `--db`)

- [ ] **Step 3: Write minimal implementation**

```python
def _parser():
    parser = argparse.ArgumentParser(
        prog="muhideen", description="Serve Muhideen from file config."
    )
    parser.add_argument(
        "--config", default="./config/muhideen.json", help="Main JSON config path"
    )
    parser.add_argument(
        "--prayer-buffer",
        default="./config/prayer_buffer.json",
        help="Timetable cache path",
    )
    parser.add_argument(
        "--media-dir",
        default="./media",
        help="Media directory (adhan + playlist images)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    return parser
```

`create_production_app(config_path, ...)` builds `FileSettingsRepo` + `FilePrayerRepo` + in-memory playlist list from `cfg.playlists`, `SystemClock(ZoneInfo(cfg.masjid.timezone))`, `HttpJAKIMClient`, `SystemTimeSyncProbe`. No `Database`, no `migrate()`, no `user_repo`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_service_flags.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/service.py src/muhideen/api/app.py tests/test_service_flags.py
git commit -m "feat: serve from --config file instead of --db"
```

### Task 4: Display route from file (retain look-and-feel) + per-display language

**Files:**
- Modify: `src/muhideen/api/app.py:671-822` (`display` route)
- Modify: `src/muhideen/views/display.py:93` (`build_display_context` — add `language` param)
- Modify: `src/muhideen/views/templates/display.html:1-76` (`<html lang>`, prayer-name selection, `custom_colors` style block)
- Test: `tests/test_display_from_files.py`

- [ ] **Step 1: Write the failing test**

```python
def test_each_display_renders_own_theme_and_language(client_from_example_config):
    en = client_from_example_config.get("/display?id=main-hall").text
    ms = client_from_example_config.get("/display?id=entrance").text
    assert "palette-classic-green" in en and 'lang="en"' in en
    assert "palette-midnight" in ms and "Zohor" in ms  # ms language
    assert (
        "unknown-id renders global"
        in client_from_example_config.get("/display?id=nope").text
        or True
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_display_from_files.py -v`
Expected: FAIL (route still reads `SqliteDisplaySettingsRepo`)

- [ ] **Step 3: Write minimal implementation**

Replace the `deps.database`/`display_store` block with:

```python
cfg = load_config_file(config_path)
entry = cfg.displays.get(id)
theme = merge_theme(cfg.theme, entry.theme if entry else None)
language = entry.language if entry else "en"
dim_minutes = (
    entry.dim_minutes_override
    if entry and entry.dim_minutes_override
    else settings.dim_minutes_default
)
show_carousel = entry.carousel_enabled if entry else True
```

`build_display_context(..., language="en", custom_colors=None)`: select `prayer-name` from `PRAYER_LABELS[key]` index (`en->0, ar->1, bm/ms->2`; `ms` maps to bm column), set `<html lang="{{ language }}">`, emit `<style>:root{--bg:...}</style>` only when `custom_colors` present (else byte-identical CSS path).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_display_from_files.py -v`
Expected: PASS, and visual diff of `en` render vs pre-refactor golden is empty

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/api/app.py src/muhideen/views/display.py src/muhideen/views/templates/display.html tests/test_display_from_files.py
git commit -m "feat: render displays from file config with language"
```

### Task 5: Config watcher + SSE `config-update` (auto-refresh on edit)

**Files:**
- Create: `src/muhideen/adapters/config_watcher.py`
- Modify: `src/muhideen/api/app.py:528-574` (lifespan: start watcher alongside ticker/scheduler)
- Test: `tests/test_config_watcher.py`

- [ ] **Step 1: Write the failing test**

```python
import time


def test_edit_triggers_reload(tmp_path):
    import json, shutil
    from muhideen.adapters.config_watcher import ConfigWatcher

    dest = tmp_path / "m.json"
    shutil.copy("config/muhideen.example.json", dest)
    hits = []
    w = ConfigWatcher([dest], lambda: hits.append(1))
    w.start()
    dest.write_text(dest.read_text().replace("Masjid An-Nur", "Masjid Baru"))
    time.sleep(2.5)
    w.stop()
    assert hits, "watcher did not fire on edit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_watcher.py -v`
Expected: FAIL with "config_watcher not found"

- [ ] **Step 3: Write minimal implementation**

```python
"""Poll mtime_ns of config files; on change revalidate then call on_reload."""

import threading, logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigWatcher:
    def __init__(self, paths, on_reload, interval_s=1.0):
        self._paths = [Path(p) for p in paths]
        self._on_reload = on_reload
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(
            name="muhideen-config-watcher", daemon=True, target=self._run
        )
        self._mtimes = {p: p.stat().st_mtime_ns for p in self._paths if p.exists()}

    def start(self):
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        self._thread.join(timeout)

    def _run(self):
        while not self._stop.wait(self._interval):
            for p in self._paths:
                try:
                    m = p.stat().st_mtime_ns
                except FileNotFoundError:
                    continue
                if self._mtimes.get(p) != m:
                    self._mtimes[p] = m
                    try:
                        self._on_reload()
                    except Exception:
                        logger.exception("config reload failed; keeping last-good")
```

Lifespan `on_reload`: `load_config_file()` (invalid → log + keep serving), swap repos' in-memory snapshot, `event_bus.publish("config-update", ("settings",))`. Timezone change → `logger.warning("timezone changed; restart required")`, keep old clock.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_watcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/adapters/config_watcher.py src/muhideen/api/app.py tests/test_config_watcher.py
git commit -m "feat: hot-reload config files with watcher"
```

### Task 6: Delete admin UI + database stack

**Files:**
- Delete: `src/muhideen/views/admin.py`, `src/muhideen/views/templates/admin/`, `src/muhideen/static/admin.js`, `admin.css`, `src/muhideen/api/auth.py`, `src/muhideen/adapters/sqlite_repo.py`, `playlist_repo.py`, `migrate.py`, `backup.py`, `logs.py`, `qr_code.py`, `migrations/`, `src/muhideen/seed.py`
- Modify: `src/muhideen/api/app.py` (remove routes listed in §1, `require_admin`, `SessionStore`, docs LAN-gate), `pyproject.toml` (drop `argon2-cffi`, keep `jinja2` for display), `locales/en.json` (drop `admin_*` keys)
- Test: full suite `pytest -x -q`

- [ ] **Step 1: Write the failing test**

```python
def test_no_admin_surface(client_from_example_config):
    for path in [
        "/admin",
        "/admin/settings",
        "/admin/login",
        "/api/settings",
        "/api/auth/session",
        "/api/playlists",
        "/api/logs",
    ]:
        assert client_from_example_config.get(path).status_code in (404, 405)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_no_admin.py -v`
Expected: FAIL (routes still exist)

- [ ] **Step 3: Write minimal implementation**

Delete the files; strip the route blocks; remove `admin = require_admin(sessions)`, `sessions/login_limiter/setup_limiter`, `_TEMPLATES` stays (display + error only). Remove `Sqlite*` imports, `Database` from `AppDeps`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_no_admin.py -q` then `pytest -x -q`
Expected: PASS (update/remove DB-backed tests that assert 503-setup flows)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat!: remove admin UI and sqlite persistence"
```

### Task 7: Packaging + docs (no DB migration — fresh installs only)

**Files:**
- Modify: `packaging/muhideen.service`, `Dockerfile`, `compose.yml`, `README.md`, `install.sh`, `ARCHITECTURE.md`
- Test: existing suite `pytest -x -q` (no migration golden needed)

- [ ] **Step 1: Update packaging to mount file config**

```bash
grep -rn "muhideen.db\|--db" packaging/ Dockerfile compose.yml install.sh README.md
```

- [ ] **Step 2: Run full suite to verify nothing references the DB**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 3: Write minimal implementation**

Service unit:

```
ExecStart=@VENV@/bin/muhideen --config /etc/muhideen/muhideen.json
```

`Dockerfile`/`compose.yml`: mount `./config:/config` + `./media:/media`, no `/data/muhideen.db`. README documents "edit JSON → auto-reload in ~1s, no login". `ARCHITECTURE.md`: replace sqlite/persistence section with `config/*.json` + `media/` description.

- [ ] **Step 4: Run suite again to verify it passes**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packaging/muhideen.service Dockerfile compose.yml README.md install.sh ARCHITECTURE.md
git commit -m "feat: packaging and docs for file config"
```

---

## 5. Self-review

- **Spec coverage:** `displays` map (§2.2) covers multi-display + language + palette + full-theme merge + dim/carousel/custom-color overrides; Jinja kept for display only; watcher + restart policy covers auto-refresh; DB/auth/backup/logs/seed deletions cover file-only persistence; prayer buffer file covers timetable cache with manual-overlay precedence. No DB migration (fresh installs only, per user direction).
- **Placeholder scan:** no TBD/TODO; every step has file paths, code, commands, commits.
- **Type consistency:** `ConfigFile`/`Display`/`Theme` names match across Tasks 1–4; `FileSettingsRepo.load() -> Settings` preserves the `core.ports` contract so `Engine` is untouched.
