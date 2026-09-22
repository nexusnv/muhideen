"""Core value objects: prayer vocabulary as frozen dataclasses.

No framework, no I/O, no wall-clock reads. Validation logic lives in
`domain/`; this module is shape only (plus the `Settings.hijri_offset`
range guard, which is a construction invariant, not computation). The guard
raises `ValueError`; config-loading adapters (1A-5) translate it to
`ConfigError` at the boundary.
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


@dataclass(frozen=True, slots=True)
class Settings:
    masjid_name: str
    zone: str
    hijri_offset: int
    adhan_duration_s: int = 180
    dim_minutes_default: int = 20
    dim_minutes_jumuah: int = 45

    def __post_init__(self) -> None:
        if not -2 <= self.hijri_offset <= 2:
            raise ValueError(f"hijri_offset out of range: {self.hijri_offset}")
