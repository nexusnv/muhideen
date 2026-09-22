"""Core value objects: prayer vocabulary as frozen dataclasses.

No framework, no I/O, no wall-clock reads. Validation logic lives in
`domain/`; this module is shape only (plus the `Settings` construction
guards — Hijri offset range and coordinate pairing/ranges — which are
invariants, not computation). The guards raise `ValueError`; config-loading
adapters (1A-5) translate them to `ConfigError` at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal


class PrayerName(StrEnum):
    FAJR = "fajr"
    SYURUQ = "syuruq"
    DHUHR = "dhuhr"
    ASR = "asr"
    MAGHRIB = "maghrib"
    ISHA = "isha"
    JUMUAH = "jumuah"


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
    date: date
    zone: str
    fajr: time
    syuruq: time
    dhuhr: time
    asr: time
    maghrib: time
    isha: time
    source: ScheduleSource
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class NextEvent:
    now: datetime
    state: PrayerState
    next_prayer: PrayerName | None
    adhan_at: datetime | None
    iqamah_at: datetime | None
    dim_until: datetime | None
    stale: bool


@dataclass(frozen=True, slots=True)
class IqamahRule:
    prayer: PrayerName
    mode: Literal["delay", "fixed"]
    delay_minutes: int = 10
    fixed_time: time | None = None


DEFAULT_IQAMAH_RULES: tuple[IqamahRule, ...] = (
    IqamahRule(prayer=PrayerName.FAJR, mode="delay", delay_minutes=15),
    IqamahRule(prayer=PrayerName.DHUHR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=PrayerName.ASR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=PrayerName.MAGHRIB, mode="delay", delay_minutes=10),
    IqamahRule(prayer=PrayerName.ISHA, mode="delay", delay_minutes=15),
    IqamahRule(prayer=PrayerName.JUMUAH, mode="delay", delay_minutes=10),
)
"""PRD FR-1.4 iqamah defaults: Subuh 15, Dhuhr/Asr/Maghrib 10, Isha 15,
Jumuah its own rule. Syuruq has no iqamah, so no rule."""


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

    def __post_init__(self) -> None:
        if not -2 <= self.hijri_offset <= 2:
            raise ValueError(f"hijri_offset out of range: {self.hijri_offset}")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        if self.lat is not None and not -90 <= self.lat <= 90:
            raise ValueError(f"latitude out of range: {self.lat}")
        if self.lon is not None and not -180 <= self.lon <= 180:
            raise ValueError(f"longitude out of range: {self.lon}")
