"""Core value objects: the two-class marker vocabulary as frozen dataclasses.

Two marker classes (PRD FR-1.7): **Prayer Time Markers** (fajr, dhuhr, asr,
maghrib, isha — jumuah replaces dhuhr on Friday) alone carry adhan, iqamah,
auto-dim, and state transitions; **Boundary Time Markers** (imsak, syuruq,
dhuha) are informational — no adhan, no iqamah, no auto-dim, never a
non-NORMAL state, opt-in countdown only.

No framework, no I/O, no wall-clock reads. Validation logic lives in
`domain/`; this module is shape only (plus the `Settings` construction
guards — Hijri offset range, coordinate pairing/ranges, and prayer-only
iqamah rules — which are invariants, not computation). The guards raise
`ValueError`; config-loading adapters (1A-5) translate them to `ConfigError`
at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal


class MarkerName(StrEnum):
    """Every time marker Muhideen acknowledges: 5 prayers + 3 boundaries.

    Declaration order is chronological, with jumuah beside dhuhr (it
    replaces dhuhr on Friday). Wire values match the API contract.
    """

    IMSAK = "imsak"
    FAJR = "fajr"
    SYURUQ = "syuruq"
    DHUHA = "dhuha"
    DHUHR = "dhuhr"
    ASR = "asr"
    MAGHRIB = "maghrib"
    ISHA = "isha"
    JUMUAH = "jumuah"


class MarkerKind(StrEnum):
    """The two marker classes: Prayer Time vs Boundary Time."""

    PRAYER = "prayer"
    BOUNDARY = "boundary"


_MARKER_KINDS: dict[MarkerName, MarkerKind] = {
    MarkerName.IMSAK: MarkerKind.BOUNDARY,
    MarkerName.FAJR: MarkerKind.PRAYER,
    MarkerName.SYURUQ: MarkerKind.BOUNDARY,
    MarkerName.DHUHA: MarkerKind.BOUNDARY,
    MarkerName.DHUHR: MarkerKind.PRAYER,
    MarkerName.ASR: MarkerKind.PRAYER,
    MarkerName.MAGHRIB: MarkerKind.PRAYER,
    MarkerName.ISHA: MarkerKind.PRAYER,
    MarkerName.JUMUAH: MarkerKind.PRAYER,
}


def marker_kind(name: MarkerName) -> MarkerKind:
    """Classify one marker; the enum is closed, so lookup cannot miss."""
    return _MARKER_KINDS[name]


class PrayerState(StrEnum):
    NORMAL = "normal"
    PRE_ADHAN = "pre_adhan"
    ADHAN = "adhan"
    IQAMAH_COUNTDOWN = "iqamah_countdown"
    SALAH_DIM = "salah_dim"


class ScheduleSource(StrEnum):
    JAKIM = "jakim"
    CALC = "calc"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class PrayerDay:
    """One day's full schedule: all 5 Prayer Time + 3 Boundary Time Markers."""

    date: date
    zone: str
    imsak: time
    fajr: time
    syuruq: time
    dhuha: time
    dhuhr: time
    asr: time
    maghrib: time
    isha: time
    source: ScheduleSource
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class NextEvent:
    """Display state for one pinned instant.

    `next_boundary`/`boundary_at` form the next Boundary Time Marker pointer —
    populated only when `Settings.boundary_countdown` is on, never a state
    input.
    """

    now: datetime
    state: PrayerState
    next_prayer: MarkerName | None
    adhan_at: datetime | None
    iqamah_at: datetime | None
    dim_until: datetime | None
    stale: bool
    next_boundary: MarkerName | None = None
    boundary_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IqamahRule:
    prayer: MarkerName
    mode: Literal["delay", "fixed"]
    delay_minutes: int = 10
    fixed_time: time | None = None


DEFAULT_IQAMAH_RULES: tuple[IqamahRule, ...] = (
    IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.DHUHR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.ASR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.MAGHRIB, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.ISHA, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=10),
)
"""PRD FR-1.4 iqamah defaults: Subuh 15, Dhuhr/Asr/Maghrib 10, Isha 15,
Jumuah its own rule. Boundary Time Markers have no iqamah, so no rule."""


@dataclass(frozen=True, slots=True)
class Settings:
    masjid_name: str
    zone: str
    hijri_offset: int
    adhan_duration_s: int = 180
    dim_minutes_default: int = 20
    dim_minutes_jumuah: int = 45
    iqamah_rules: tuple[IqamahRule, ...] = DEFAULT_IQAMAH_RULES
    lat: float | None = None
    lon: float | None = None
    method: str = "MABIMS"
    boundary_countdown: bool = False
    calc_only: bool = False

    def __post_init__(self) -> None:
        if not -2 <= self.hijri_offset <= 2:
            raise ValueError(f"hijri_offset out of range: {self.hijri_offset}")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        if self.lat is not None and not -90 <= self.lat <= 90:
            raise ValueError(f"latitude out of range: {self.lat}")
        if self.lon is not None and not -180 <= self.lon <= 180:
            raise ValueError(f"longitude out of range: {self.lon}")
        seen: set[MarkerName] = set()
        for rule in self.iqamah_rules:
            if marker_kind(rule.prayer) is MarkerKind.BOUNDARY:
                raise ValueError(
                    f"boundary time marker cannot have an iqamah rule: "
                    f"{rule.prayer.value}"
                )
            if rule.mode == "fixed" and rule.fixed_time is None:
                raise ValueError(
                    f"fixed iqamah rule without time for prayer: {rule.prayer.value}"
                )
            if rule.prayer in seen:
                raise ValueError(
                    f"duplicate iqamah rule for prayer: {rule.prayer.value}"
                )
            seen.add(rule.prayer)
        if self.iqamah_rules:
            # A non-empty set must cover every Prayer Time Marker exactly once
            # (FR-1.4): partial sets 503 every read endpoint at resolve time.
            # Empty stays legal — the repo falls back to defaults and resolve
            # raises ConfigError for deferred configuration.
            prayer_markers = {
                name
                for name, kind in _MARKER_KINDS.items()
                if kind is MarkerKind.PRAYER
            }
            missing = sorted(prayer_markers - seen, key=lambda name: name.value)
            if missing:
                raise ValueError(
                    "missing iqamah rule for prayer: "
                    + ", ".join(name.value for name in missing)
                )
