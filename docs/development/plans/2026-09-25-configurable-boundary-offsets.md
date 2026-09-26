# Configurable Boundary Offsets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make imsak/dhuha calc offsets admin-configurable (imsak 0–10 default 10, dhuha 15–30 default 28) with API-days winning per marker and imsak 0 meaning hidden.

**Architecture:** Settings VO owns range guards; `compute_day` takes keyword-only offsets (defaults preserve new behavior); engine passes settings offsets per call and merges cached+calc per marker (cached wins; complete rows today make this equal whole-day precedence); ordering relaxes only the imsak–fajr pair to `<=` (equality is the disabled signal); API keeps returning `imsak == fajr` when 0 and the display hides it.

**Tech Stack:** Python 3.11+, adhanpy 1.0.5 (6 markers: fajr/sunrise/dhuhr/asr/maghrib/isha; imsak+dhuha derived), SQLite key-value settings (no new migration file), Pydantic SettingsDTO, pytest (unit/contract/integration/e2e).

**Starting state:** Working tree has one uncommitted docstring note in `src/muhideen/adapters/calc_mabims.py:3-5` (adhanpy 6-marker note). Keep it; Task 4 rewrites the surrounding docstring while preserving that note.

---

## File map

- Modify: `src/muhideen/core/values.py` — 2 new Settings fields + guards.
- Modify: `src/muhideen/domain/ordering.py` — first pair `<=`, rest strict.
- Modify: `src/muhideen/domain/fallback.py` — add `merge_days`.
- Modify: `src/muhideen/core/ports.py` — CalcEngine signature.
- Modify: `src/muhideen/adapters/calc_mabims.py` — offset params, fixed defaults, drop latitude rule.
- Modify: `src/muhideen/engine/engine.py` — pass offsets, use merge.
- Modify: `src/muhideen/adapters/sqlite_repo.py` — load/save new keys.
- Modify: `src/muhideen/migrations/0001_initial.sql` — seeds + key comment.
- Modify: `src/muhideen/api/dto.py` — SettingsDTO fields + mappers.
- Modify: `api/fixtures/settings.json` — new keys.
- Modify: `docs/api-contract.md` — GET/PUT settings examples.
- Modify: `PRD.md`, `ARCHITECTURE.md`, `CHANGELOG.md` — requirements/architecture/changelog.
- Tests: `tests/unit/test_core_values.py`, `tests/unit/test_domain_ordering.py`, `tests/unit/test_domain_fallback.py`, `tests/unit/test_core_ports.py`, `tests/integration/test_calc_mabims.py`, `tests/integration/test_engine.py`, `tests/integration/test_sqlite_settings_repo.py`, `tests/e2e/test_admin_settings.py` (contract tests run unchanged).

---

### Task 1: Settings value object guards

**Files:**
- Modify: `src/muhideen/core/values.py:147-163`
- Test: `tests/unit/test_core_values.py`

- [ ] **Step 1: Write the failing test**

```python
def test_settings_boundary_offset_defaults_and_guards() -> None:
    from muhideen.core.values import Settings

    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.imsak_offset_min == 10
    assert settings.dhuha_offset_min == 28


def test_settings_boundary_offset_ranges_rejected() -> None:
    import pytest

    from muhideen.core.values import Settings

    for bad in (-1, 11):
        with pytest.raises(ValueError):
            Settings(masjid_name="M", zone="SGR01", hijri_offset=0, imsak_offset_min=bad)
    for bad in (14, 31):
        with pytest.raises(ValueError):
            Settings(masjid_name="M", zone="SGR01", hijri_offset=0, dhuha_offset_min=bad)


def test_settings_boundary_offset_edges_accepted() -> None:
    from muhideen.core.values import Settings

    assert Settings(masjid_name="M", zone="SGR01", hijri_offset=0, imsak_offset_min=0).imsak_offset_min == 0
    assert Settings(masjid_name="M", zone="SGR01", hijri_offset=0, dhuha_offset_min=15).dhuha_offset_min == 15
    assert Settings(masjid_name="M", zone="SGR01", hijri_offset=0, dhuha_offset_min=30).dhuha_offset_min == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_core_values.py -q -k "boundary_offset" 2>&1 | tail -5`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'imsak_offset_min'`

- [ ] **Step 3: Write minimal implementation**

In `src/muhideen/core/values.py`, extend the Settings dataclass fields (after `calc_only`):

```python
    boundary_countdown: bool = False
    calc_only: bool = False
    imsak_offset_min: int = 10
    dhuha_offset_min: int = 28
```

In `__post_init__`, after the longitude guard and before the iqamah loop, insert:

```python
        if not 0 <= self.imsak_offset_min <= 10:
            raise ValueError(f"imsak_offset_min out of range: {self.imsak_offset_min}")
        if not 15 <= self.dhuha_offset_min <= 30:
            raise ValueError(f"dhuha_offset_min out of range: {self.dhuha_offset_min}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_core_values.py -q 2>&1 | tail -3`
Expected: PASS (all tests in file)

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/core/values.py tests/unit/test_core_values.py
git commit -m "feat: configurable imsak/dhuha offsets on Settings with range guards"
```

---

### Task 2: Ordering allows imsak == fajr (disabled signal)

**Files:**
- Modify: `src/muhideen/domain/ordering.py:1-54`
- Test: `tests/unit/test_domain_ordering.py`

- [ ] **Step 1: Write the failing test**

```python
def test_imsak_equal_fajr_passes_as_disabled_signal() -> None:
    from muhideen.domain.ordering import ensure_ordered

    base = _day()
    disabled = replace(base, imsak=base.fajr)
    assert ensure_ordered(disabled) is disabled
```

Append to `tests/unit/test_domain_ordering.py` (uses its existing `_day` and `replace` imports).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_domain_ordering.py -q 2>&1 | tail -5`
Expected: FAIL with `SyncError: time order violated: imsak ...`

- [ ] **Step 3: Write minimal implementation**

In `src/muhideen/domain/ordering.py`, update the module docstring chain line:

```python
"""Chronological ordering invariant for a day's eight markers (PRD §6.1).

One pure validator shared by both schedule sources: parsed JAKIM rows and
calc-produced days must satisfy ``Imsak <= Fajr < Syuruq < Dhuha < Dhuhr
< Asr < Maghrib < Isha``. Equality on the first pair only is the imsak
disabled signal (calc ``imsak_offset_min=0`` yields ``imsak == fajr``;
JAKIM rows are strict in practice). The repo still stores whatever it is
given; rejection happens at the source adapters, before any write.
"""
```

Replace the comparison loop:

```python
    for index, ((left_name, left_value), (right_name, right_value)) in enumerate(
        zip(slots, slots[1:], strict=False)
    ):
        ok = left_value <= right_value if index == 0 else left_value < right_value
        if not ok:
            raise SyncError(
                f"time order violated: {left_name.value} {left_value} "
                f"!<{'' if index else '='} {right_name.value} {right_value}",
                zone=day.zone,
                date=day.date.isoformat(),
            )
    return day
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_domain_ordering.py -q 2>&1 | tail -3`
Expected: PASS. Existing `test_equal_adjacent_times_raise` (syuruq==fajr) still passes; the imsak/fajr swap case still raises since swapped imsak > fajr.

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/domain/ordering.py tests/unit/test_domain_ordering.py
git commit -m "feat: allow imsak==fajr as disabled signal in ordering"
```

---

### Task 3: Per-marker merge helper

**Files:**
- Modify: `src/muhideen/domain/fallback.py`
- Test: `tests/unit/test_domain_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
def test_merge_days_prefers_cached_markers() -> None:
    from datetime import date, time
    from zoneinfo import ZoneInfo

    from muhideen.core.values import PrayerDay, ScheduleSource
    from muhideen.domain.fallback import merge_days

    tz = ZoneInfo("Asia/Kuala_Lumpur")
    from datetime import datetime as _dt

    cached = PrayerDay(
        date=date(2026, 9, 23), zone="SGR01",
        imsak=time(5, 45), fajr=time(5, 55), syuruq=time(7, 1),
        dhuha=time(7, 26), dhuhr=time(13, 9), asr=time(16, 14),
        maghrib=time(19, 11), isha=time(20, 20),
        source=ScheduleSource.JAKIM,
        fetched_at=_dt(2026, 9, 23, 1, 0, tzinfo=tz),
    )
    calc = PrayerDay(
        date=date(2026, 9, 23), zone="SGR01",
        imsak=time(5, 40), fajr=time(5, 50), syuruq=time(7, 0),
        dhuha=time(7, 30), dhuhr=time(12, 20), asr=time(15, 35),
        maghrib=time(18, 10), isha=time(19, 30),
        source=ScheduleSource.CALC,
        fetched_at=_dt(2026, 9, 23, 1, 0, tzinfo=tz),
    )
    merged = merge_days(cached, calc)
    assert merged is not None
    assert (merged.fajr, merged.dhuhr, merged.source) == (time(5, 55), time(13, 9), ScheduleSource.JAKIM)


def test_merge_days_falls_back_to_calc() -> None:
    from datetime import date, time
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from muhideen.core.values import PrayerDay, ScheduleSource
    from muhideen.domain.fallback import merge_days

    tz = ZoneInfo("Asia/Kuala_Lumpur")
    calc = PrayerDay(
        date=date(2026, 9, 23), zone="SGR01",
        imsak=time(5, 40), fajr=time(5, 50), syuruq=time(7, 0),
        dhuha=time(7, 30), dhuhr=time(12, 20), asr=time(15, 35),
        maghrib=time(18, 10), isha=time(19, 30),
        source=ScheduleSource.CALC,
        fetched_at=_dt(2026, 9, 23, 1, 0, tzinfo=tz),
    )
    assert merge_days(None, calc) == calc
    assert merge_days(None, None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_domain_fallback.py -q -k merge 2>&1 | tail -5`
Expected: FAIL with `ImportError: cannot import name 'merge_days'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/muhideen/domain/fallback.py`:

```python
def merge_days(preferred: PrayerDay | None, fallback: PrayerDay | None) -> PrayerDay | None:
    """Merge two complete days per marker, preferring ``preferred``.

    API (cached/JAKIM) markers supersede calc markers one by one. Rows are
    complete today (the JAKIM parser rejects partial payloads and the DB
    columns are NOT NULL), so a present ``preferred`` day wins wholesale —
    the per-marker form is what future-proofs a partial-row world without a
    schema change. Provenance follows ``preferred``.
    """
    if preferred is None:
        return fallback
    if fallback is None:
        return preferred
    return PrayerDay(
        date=preferred.date,
        zone=preferred.zone,
        imsak=preferred.imsak,
        fajr=preferred.fajr,
        syuruq=preferred.syuruq,
        dhuha=preferred.dhuha,
        dhuhr=preferred.dhuhr,
        asr=preferred.asr,
        maghrib=preferred.maghrib,
        isha=preferred.isha,
        source=preferred.source,
        fetched_at=preferred.fetched_at,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_domain_fallback.py -q 2>&1 | tail -3`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/domain/fallback.py tests/unit/test_domain_fallback.py
git commit -m "feat: per-marker merge prefers cached day markers"
```

---

### Task 4: CalcEngine port + MABIMS implementation with offsets

**Files:**
- Modify: `src/muhideen/core/ports.py:75-81`
- Modify: `src/muhideen/adapters/calc_mabims.py`
- Test: `tests/unit/test_core_ports.py`, `tests/integration/test_calc_mabims.py` (touched in Task 5; port test here)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_core_ports.py`, update `FullCalcEngine` to the new signature:

```python
class FullCalcEngine:
    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = 10,
        dhuha_offset_min: int = 28,
    ) -> PrayerDay:
        raise NotImplementedError
```

Run: `uv run pytest tests/unit/test_core_ports.py -q 2>&1 | tail -3` → Expected: PASS after edit (protocol check), but first add an isinstance assertion test if missing. The real red is the engine test in Task 6; for this task the red is:

```python
def test_calc_engine_port_accepts_offsets() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    import inspect

    params = inspect.signature(MabimsCalcEngine.compute_day).parameters
    assert "imsak_offset_min" in params and "dhuha_offset_min" in params
```

Append that to `tests/unit/test_core_ports.py` (add `import inspect` locally in the test).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_core_ports.py -q -k offsets 2>&1 | tail -5`
Expected: FAIL with `AssertionError`

- [ ] **Step 3: Write minimal implementation**

`src/muhideen/core/ports.py`:

```python
@runtime_checkable
class CalcEngine(Protocol):
    """On-device prayer-time calculator for configured coordinates."""

    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = 10,
        dhuha_offset_min: int = 28,
    ) -> PrayerDay:
        """Compute one day's markers; raise ``ValueError`` for unknown methods."""
        ...
```

`src/muhideen/adapters/calc_mabims.py` — replace constants block:

```python
FAJR_ANGLE_DEG = 17.75
ISHA_ANGLE_DEG = 18.25
DHUHR_TUNE_MIN = 2
DEFAULT_IMSAK_OFFSET_MIN = 10
DEFAULT_DHUHA_OFFSET_MIN = 28
```

Delete `_dhuha_offset_min` entirely. New `compute_day`:

```python
    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = DEFAULT_IMSAK_OFFSET_MIN,
        dhuha_offset_min: int = DEFAULT_DHUHA_OFFSET_MIN,
    ) -> PrayerDay:
        """Compute one day's eight markers via adhanpy; `ValueError` if unsupported.

        adhanpy supplies 6 markers (fajr, sunrise→syuruq, dhuhr, asr,
        maghrib, isha); imsak/dhuha are derived offsets
        (``imsak = fajr − imsak_offset_min`` with 0 meaning disabled/hidden,
        ``dhuha = syuruq + dhuha_offset_min``). Out-of-range offsets are a
        cache miss (`ValueError`), matching the Settings guards (0–10, 15–30).
        The result is stamped `ScheduleSource.CALC` with the pinned clock
        and passed through `ensure_ordered` before anything returns.
        """
        if method != _PINNED_METHOD:
            # engine.py converts ValueError to a cache miss, so an unknown
            # configured method degrades the chain instead of 500-ing.
            raise ValueError(f"unsupported calculation method: {method}")
        if not 0 <= imsak_offset_min <= 10:
            raise ValueError(f"imsak_offset_min out of range: {imsak_offset_min}")
        if not 15 <= dhuha_offset_min <= 30:
            raise ValueError(f"dhuha_offset_min out of range: {dhuha_offset_min}")
        times = PrayerTimes(
            (lat, lon),
            datetime(day.year, day.month, day.day),
            calculation_parameters=_params(),
            time_zone=self._tz,
        )
        fajr: time = times.fajr.time()
        syuruq: time = times.sunrise.time()
        imsak = (
            datetime.combine(day, fajr) - timedelta(minutes=imsak_offset_min)
        ).time()
        dhuha = (
            datetime.combine(day, syuruq) + timedelta(minutes=dhuha_offset_min)
        ).time()
```

Also update the module docstring params line (keep the 6-marker note from the working tree):

```python
"""On-device MABIMS prayer-time calculation (PRD FR-1.2's calc fallback).

adhanpy's ``PrayerTimes`` surfaces 6 markers only (fajr, sunrise, dhuhr,
asr, maghrib, isha); ``imsak`` and ``dhuha`` are derived offsets, not
library outputs. ``sunrise`` maps to backend ``syuruq``.

Parameters are pinned by the recorded source research: fajr 17.75°,
isha 18.25°, Asr shadow factor 1 (Standard/MABIMS), dhuhr tune +2 min,
imsak = fajr − ``imsak_offset_min`` (default 10, 0 disables/hides),
dhuha = syuruq + ``dhuha_offset_min`` (default 28); fitted to JAKIM's
published tables with pooled max|e| = 5 minutes (`GOLDEN_TOLERANCE_MIN`,
asserted by the golden test). Computation library: adhanpy 1.0.5 (MIT,
zero runtime deps) behind the `CalcEngine` port — swapping it is bounded
rework pinned by those golden tests.

`zone` is left empty on purpose: the engine stamps it (`engine.py`
`replace(computed, zone=zone)`) because calc is zone-agnostic.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_core_ports.py -q 2>&1 | tail -3`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/core/ports.py src/muhideen/adapters/calc_mabims.py tests/unit/test_core_ports.py
git commit -m "feat: calc engine accepts imsak/dhuha offsets with guards"
```

---

### Task 5: Retune golden tests to fixed defaults

**Files:**
- Modify: `tests/integration/test_calc_mabims.py`
- Test: same file

- [ ] **Step 1: Write the failing test**

Change `GOLDEN_TOLERANCE_MIN = 3` to `5` is the fix itself; the red comes from running the existing suite against Task 4's fixed-28 behavior:

Run: `uv run pytest tests/integration/test_calc_mabims.py -q 2>&1 | tail -5`
Expected: FAIL on SGR01 dhuha vectors (`+5 vs tolerance 3`)

Then add offset-behavior tests:

```python
def test_imsak_zero_disables_to_fajr() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    computed = engine.compute_day(
        date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", imsak_offset_min=0
    )
    assert computed.imsak == computed.fajr


def test_dhuha_offset_param honored() -> None:
    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    computed = engine.compute_day(
        date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", dhuha_offset_min=15
    )
    assert (computed.dhuha.hour * 60 + computed.dhuha.minute) - (
        computed.syuruq.hour * 60 + computed.syuruq.minute
    ) == 15


def test_out_of_range_offsets_raise() -> None:
    import pytest

    from muhideen.adapters.calc_mabims import MabimsCalcEngine

    engine = MabimsCalcEngine(clock=FakeClock(PINNED), tz=TZ)
    with pytest.raises(ValueError):
        engine.compute_day(date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", imsak_offset_min=11)
    with pytest.raises(ValueError):
        engine.compute_day(date(2026, 9, 23), 3.0738, 101.5167, "MABIMS", dhuha_offset_min=14)
```

Also update the module docstring scope line: `Pinned scope: **6 functions / 16 items**` → `**9 functions / 19 items**`, and `GOLDEN_TOLERANCE_MIN` note `3` → `5 (fixed-28 default; SGR01 dhuha +5 worst case)`. Update `test_imsak_is_fajr_minus_10` name? Keep name (default behavior) — no rename to avoid churn.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_calc_mabims.py -q 2>&1 | tail -5`
Expected: FAIL (golden dhuha SGR01 exceeds 3; new offset tests missing → first run fails on golden)

- [ ] **Step 3: Write minimal implementation (test-only task)**

Set `GOLDEN_TOLERANCE_MIN = 5`, add the three tests above, update docstring scope line.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_calc_mabims.py -q 2>&1 | tail -3`
Expected: PASS (19 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_calc_mabims.py
git commit -m "test: golden tolerance 5 for fixed-28 dhuha plus offset guards"
```

---

### Task 6: Engine plumbing + fakes

**Files:**
- Modify: `src/muhideen/engine/engine.py:186-200`
- Modify: `tests/integration/test_engine.py:76-101` (FakeCalc)
- Test: `tests/integration/test_engine.py`

- [ ] **Step 1: Write the failing test**

Update `FakeCalc` in `tests/integration/test_engine.py`:

```python
class FakeCalc:
    """Calc port double: records calls, draws a CALC day, can raise."""

    def __init__(self, returned_zone: str = ZONE) -> None:
        self.returned_zone = returned_zone
        self.calls: list[tuple[date, float, float, str, int, int]] = []
        self.error: Exception | None = None

    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = 10,
        dhuha_offset_min: int = 28,
    ) -> PrayerDay:
        self.calls.append((day, lat, lon, method, imsak_offset_min, dhuha_offset_min))
        if self.error is not None:
            raise self.error
        return PrayerDay(
            date=day,
            zone=self.returned_zone,
            imsak=time(5, 40),
            fajr=time(5, 50),
            syuruq=time(7, 0),
            dhuha=time(7, 30),
            dhuhr=time(12, 20),
            asr=time(15, 35),
            maghrib=time(18, 10),
            isha=time(19, 30),
            source=ScheduleSource.CALC,
            fetched_at=datetime.combine(day, time(0, 0), tzinfo=TZ),
        )
```

Add:

```python
def test_calc_receives_settings_offsets() -> None:
    harness = _harness()
    harness.settings.settings = replace(
        harness.settings.settings, imsak_offset_min=5, dhuha_offset_min=20
    )
    engine = Engine(
        settings_repo=harness.settings,
        prayer_repo=harness.prayers,
        clock=harness.clock,
        event_bus=harness.bus,
        calc=harness.calc,
    )
    engine.resolve_day(date(2026, 9, 23), ZONE, harness.clock.now())
    assert harness.calc.calls[0][4:] == (5, 20)
```

(Uses the file's existing `_harness`, `ZONE`, `replace`, `date` imports — all already present in that file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_engine.py -q -k "receives_settings_offsets" 2>&1 | tail -5`
Expected: FAIL with `TypeError: compute_day() got an unexpected keyword argument`

- [ ] **Step 3: Write minimal implementation**

In `src/muhideen/engine/engine.py`, update imports:

```python
from muhideen.domain import FallbackResult, resolve_next_event
from muhideen.domain import resolve_day as resolve_fallback
from muhideen.domain.fallback import merge_days
```

Replace `_resolve_day` body tail:

```python
        cached = self._prayer_repo.get_day(requested, zone)
        calculated = (
            self._calc_day(requested, zone, settings) if cached is None else None
        )
        last_known = (
            self._prayer_repo.last_known(requested, zone)
            if cached is None and calculated is None
            else None
        )
        merged = merge_days(cached, calculated)
        return resolve_fallback(requested, zone, now, merged, None, last_known)
```

Replace `_calc_day` compute call:

```python
            computed = self._calc.compute_day(
                day,
                settings.lat,
                settings.lon,
                settings.method,
                imsak_offset_min=settings.imsak_offset_min,
                dhuha_offset_min=settings.dhuha_offset_min,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_engine.py tests/integration/test_sync_stale.py tests/integration/test_engine_sqlite.py -q 2>&1 | tail -3`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/engine/engine.py tests/integration/test_engine.py
git commit -m "feat: engine passes offsets and merges per marker"
```

---

### Task 7: Persistence (repo + seeds)

**Files:**
- Modify: `src/muhideen/adapters/sqlite_repo.py:216-272`
- Modify: `src/muhideen/migrations/0001_initial.sql:4-11,67-73`
- Test: `tests/integration/test_sqlite_settings_repo.py`

- [ ] **Step 1: Write the failing test**

```python
def test_offset_keys_round_trip() -> None:
    from muhideen.core.values import Settings

    settings = Settings(
        masjid_name="Masjid Test", zone="SGR01", hijri_offset=0,
        imsak_offset_min=5, dhuha_offset_min=20,
    )
    repo.save(settings)
    loaded = repo.load()
    assert (loaded.imsak_offset_min, loaded.dhuha_offset_min) == (5, 20)


def test_offset_keys_default_when_missing() -> None:
    from muhideen.core.values import Settings

    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with repo._db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('imsak_offset_min', 'dhuha_offset_min')")
    loaded = repo.load()
    assert (loaded.imsak_offset_min, loaded.dhuha_offset_min) == (10, 28)
```

(Uses the file's existing `repo` fixture name — check the file's fixture; if named differently, use it. The round-trip style follows the existing `test_*` functions in that file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_sqlite_settings_repo.py -q -k offset 2>&1 | tail -5`
Expected: FAIL with `TypeError` (unknown kwargs) or `AssertionError` on load

- [ ] **Step 3: Write minimal implementation**

In `SqliteSettingsRepo.load`, add:

```python
                imsak_offset_min=int(kv.get("imsak_offset_min", "10")),
                dhuha_offset_min=int(kv.get("dhuha_offset_min", "28")),
```

In `save`, extend `pairs`:

```python
            ("method", settings.method),
            ("imsak_offset_min", str(settings.imsak_offset_min)),
            ("dhuha_offset_min", str(settings.dhuha_offset_min)),
```

In `0001_initial.sql`, update the keys comment:

```sql
  -- keys: masjid_name, zone_code|lat,lon,method, hijri_offset(-2..2),
  -- adhan_duration_s, dim_minutes_default, dim_minutes_jumuah,
  -- imsak_offset_min(0..10), dhuha_offset_min(15..30),
  -- boundary_countdown(0|1), calc_only(0|1), carousel_enabled, theme_default,
  -- qr_visible_default
```

And seeds:

```sql
INSERT OR IGNORE INTO settings (key, value) VALUES
  ('hijri_offset', '0'),
  ('adhan_duration_s', '180'),
  ('dim_minutes_default', '20'),
  ('dim_minutes_jumuah', '45'),
  ('imsak_offset_min', '10'),
  ('dhuha_offset_min', '28'),
  ('boundary_countdown', '0'),
  ('method', 'MABIMS');
```

No new migration file: key-value additive keys fall back to VO defaults on old DBs (same precedent as `adhan_duration_s`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_sqlite_settings_repo.py tests/integration/test_migrate.py tests/integration/test_seed.py -q 2>&1 | tail -3`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/adapters/sqlite_repo.py src/muhideen/migrations/0001_initial.sql tests/integration/test_sqlite_settings_repo.py
git commit -m "feat: persist imsak/dhuha offsets with seeds and defaults"
```

---

### Task 8: API contract (DTO + fixtures + docs)

**Files:**
- Modify: `src/muhideen/api/dto.py:298-349`
- Modify: `api/fixtures/settings.json`
- Modify: `docs/api-contract.md:69-123`
- Test: `tests/contract/test_fixtures.py`, `tests/contract/test_api_contract_doc.py`, `tests/contract/test_openapi_parity.py`, `tests/e2e/test_admin_settings.py`

- [ ] **Step 1: Write the failing test**

Run: `uv run pytest tests/contract/test_fixtures.py -q 2>&1 | tail -5`
Expected: FAIL after DTO change (fixture missing new required fields) — DTO change comes first in Step 3, so run this after Step 3 to see the red, then fix fixtures. For TDD order: first add DTO fields (Step 3a), run contract tests to see fixture red, then update fixtures/docs (Step 3b). The e2e addition:

```python
def test_offset_round_trip(surface: SimpleNamespace, client: TestClient) -> None:
    payload = _settings_payload(imsak_offset_min=5, dhuha_offset_min=20)
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert (body["imsak_offset_min"], body["dhuha_offset_min"]) == (5, 20)
    assert client.get("/api/settings").json()["imsak_offset_min"] == 5
```

(Uses the file's existing `_settings_payload`, `surface`, `client` helpers.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_admin_settings.py -q -k offset 2>&1 | tail -5`
Expected: FAIL with `TypeError: _settings_payload() got an unexpected keyword argument` (before helper passes through) or 422 from DTO

- [ ] **Step 3: Write minimal implementation**

`src/muhideen/api/dto.py` SettingsDTO — add after `calc_only`:

```python
    boundary_countdown: bool
    calc_only: bool
    imsak_offset_min: Annotated[int, Field(ge=0, le=10)]
    dhuha_offset_min: Annotated[int, Field(ge=15, le=30)]
```

`from_domain` add:

```python
            boundary_countdown=settings.boundary_countdown,
            calc_only=settings.calc_only,
            imsak_offset_min=settings.imsak_offset_min,
            dhuha_offset_min=settings.dhuha_offset_min,
```

`to_domain` add:

```python
            boundary_countdown=self.boundary_countdown,
            calc_only=self.calc_only,
            imsak_offset_min=self.imsak_offset_min,
            dhuha_offset_min=self.dhuha_offset_min,
```

`api/fixtures/settings.json` — add `"imsak_offset_min":10,"dhuha_offset_min":28` (keep alphabetical-ish order with existing keys; exact bytes: insert after `"hijri_offset":0,` → `"hijri_offset":0,"imsak_offset_min":10,` and after `"method":"MABIMS",` → `"dhuha_offset_min":28,` — verify with the contract test).

`docs/api-contract.md` — in both GET and PUT settings examples, add after `"hijri_offset": 0,`:

```json
  "imsak_offset_min": 10,
  "dhuha_offset_min": 28,
```

Place `imsak_offset_min` after `hijri_offset` and `dhuha_offset_min` after `method` to mirror the fixture. Also amend the section intro line: `Full installation settings including the boundary offsets (imsak 0–10 default 10 with 0 hiding imsak on display; dhuha 15–30 default 28), the boundary_countdown opt-in and calc_only offline mode.`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_fixtures.py tests/contract/test_api_contract_doc.py tests/contract/test_openapi_parity.py tests/e2e/test_admin_settings.py -q 2>&1 | tail -3`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/muhideen/api/dto.py api/fixtures/settings.json docs/api-contract.md tests/e2e/test_admin_settings.py
git commit -m "feat: expose imsak/dhuha offsets in settings API with fixtures"
```

---

### Task 9: Requirements, architecture, changelog

**Files:**
- Modify: `PRD.md:237,247-250`
- Modify: `ARCHITECTURE.md:110-116`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write the failing test**

No code test — verification is `git diff --stat` showing the three files plus a grep:

Run: `rg -n "imsak_offset_min" PRD.md ARCHITECTURE.md CHANGELOG.md api/fixtures/settings.json docs/api-contract.md | head`
Expected: FAIL (no hits in PRD/ARCHITECTURE/CHANGELOG yet)

- [ ] **Step 2: Run to verify it fails**

Same command. Expected: only fixture/contract hits.

- [ ] **Step 3: Write minimal implementation**

PRD §6.1: replace `Boundary derivations pinned by source research over both zones' full-2026 tables: \`imsak = fajr − 10 min\` exactly; \`dhuha = syuruk + per-zone offset\` (+25 min SGR01, +27 min KDH01) — \`calc_mabims\` fits the single latitude-based rule \`offset_min = round(22.9851 + 0.6555 × latitude)\` within the pinned golden tolerance of 3 minutes.`
with:
`Boundary offsets (calc mode; JAKIM rows always carry all 8 markers exactly): \`imsak = fajr − imsak_offset_min\` (default 10, range 0–10; 0 means disabled — API returns \`imsak == fajr\` and the display hides the imsak marker); \`dhuha = syuruq + dhuha_offset_min\` (default 28, range 15–30). JAKIM tables show imsak exactly fajr−10 and dhuha syuruk+25 (SGR01)/+27 (KDH01); the fixed calc defaults trade exact fit for simplicity within the pinned golden tolerance of 5 minutes.`

PRD §6.2 settings comment: add `imsak_offset_min(0..10), dhuha_offset_min(15..30)` to the keys list.

ARCHITECTURE.md Acquire paragraph: replace `The two sources never mix inside one day: each stored day carries its source, and each fetch either fully validates or leaves the cache untouched.`
with:
`Each stored day carries its source, and each fetch either fully validates or leaves the cache untouched. At resolve time markers merge per marker with cached (API) markers winning over calc; rows are complete today so a present cached day wins wholesale.`

ARCHITECTURE.md Resolve list: after the 4-line priority block add:
`Per-marker precedence: when both candidates exist, each marker takes the cached value; provenance follows the cached day. Calc derivation itself is per marker: 6 from the library plus imsak/dhuha offsets (0 hides imsak).`

CHANGELOG `[Unreleased] Added`: `Configurable calc boundary offsets: imsak_offset_min (0–10, default 10; 0 returns imsak==fajr and hides the marker on display) and dhuha_offset_min (15–30, default 28); fixed defaults replace the latitude-fitted dhuha rule (golden tolerance 3→5); settings API/fixtures/contract carry both keys; resolve merges per marker with API winning.`

- [ ] **Step 4: Run to verify it passes**

Run: `rg -n "imsak_offset_min" PRD.md ARCHITECTURE.md CHANGELOG.md | head`
Expected: hits in all three. Then: `uv run pytest -m contract -q 2>&1 | tail -2` PASS.

- [ ] **Step 5: Commit**

```bash
git add PRD.md ARCHITECTURE.md CHANGELOG.md
git commit -m "docs: pin configurable boundary offsets and per-marker precedence"
```

---

### Task 10: Full quality gate

- [ ] **Step 1: Run purity scan**

Run: `rg -n "fastapi|httpx|datetime\.now|time\.time" src/muhideen/domain/ src/muhideen/engine/ src/muhideen/core/ 2>&1 | tail -3`
Expected: 0 hits (no output)

- [ ] **Step 2: Run lint + types**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright 2>&1 | tail -5`
Expected: all green

- [ ] **Step 3: Run full suite + coverage**

Run: `uv run pytest -q 2>&1 | tail -3`
Expected: PASS (whole suite green)

Run: `uv run pytest --cov=muhideen --cov-report=term-missing 2>&1 | tail -8`
Expected: coverage ≥95 (new branches — offset guards, merge fallbacks, imsak-equality path — all have named tests)

- [ ] **Step 4: Verify no dev-doc references leak into shipped files**

Run: `rg -n "docs/development" src/muhideen/ docs/api-contract.md PRD.md ARCHITECTURE.md CHANGELOG.md 2>&1 | tail -3`
Expected: 0 hits

- [ ] **Step 5: Commit (only if gate made changes; otherwise no-op)**

```bash
git status --short
```

---

## Self-review

1. **Spec coverage:** imsak default 10 range 0–10 + hide-on-0 → Tasks 1, 4, 5, 7, 8, 9. Dhuha default 28 range 15–30 → same tasks. API supersedes calc → Task 3 merge + Task 6 wiring (cached wins per marker). Per-marker merge → Task 3 + Task 6 (explicit function + engine use + tests). Calc-only markers note → Task 4 docstring. Gap: no per-marker *partial-row* persistence (nullable markers, mixed source) — deliberately deferred: JAKIM rows are atomic-complete (730/730 + live verified) and DB columns are NOT NULL; merge over complete days satisfies the voted choice without schema churn. Stated in Task 3 docstring and Task 9 changelog scope.
2. **Placeholder scan:** no TBD/TODO/appropriate/similar-to-Task — each step carries exact code and commands. Task 7's fixture-name caveat names the fallback (follow the file's existing fixture) without leaving behavior unspecified.
3. **Type consistency:** `imsak_offset_min`/`dhuha_offset_min` plain ints across Settings, port, engine, repo, DTO, fixtures. `merge_days` returns `PrayerDay | None`, matching `resolve_fallback` inputs. FakeCalc mirrors the real signature with identical defaults. Tolerance constant is `5` in both implementation docstring and golden test.
