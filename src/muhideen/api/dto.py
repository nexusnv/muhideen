"""Pydantic DTOs: the executable API contract (slice 1A-3).

Wire formats follow ``docs/api-contract.md`` exactly: uppercase ``state``
values, ``HH:MM`` prayer times, tz-aware ISO8601 datetimes, and
``extra="forbid"`` everywhere so any drift fails validation. Mappers convert
from ``muhideen.core`` value objects; this module never reads the clock and
performs no business logic (that is ``domain/``'s job).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from muhideen.core.values import (
    NextEvent,
    PrayerDay,
    PrayerName,
    PrayerState,
    ScheduleSource,
)

StateLiteral = Literal[
    "NORMAL", "PRE_ADHAN", "ADHAN", "IQAMAH_COUNTDOWN", "SALAH_DIM"
]
PrayerLiteral = Literal["fajr", "syuruq", "dhuhr", "asr", "maghrib", "isha", "jumuah"]
TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class ContractDTO(BaseModel):
    """Shared wire rules: no extra fields, tz-aware datetimes only."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _reject_naive_datetimes(cls, value: object) -> object:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("datetime fields must be tz-aware (ISO8601 offset)")
        return value


class PrayerTimesDTO(ContractDTO):
    fajr: TimeHHMM
    syuruq: TimeHHMM
    dhuhr: TimeHHMM
    asr: TimeHHMM
    maghrib: TimeHHMM
    isha: TimeHHMM


class PrayerDayDTO(ContractDTO):
    date: date
    zone: str
    times: PrayerTimesDTO
    source: ScheduleSource
    stale: bool

    @classmethod
    def from_domain(cls, day: PrayerDay, stale: bool) -> PrayerDayDTO:
        return cls(
            date=day.date,
            zone=day.zone,
            times=PrayerTimesDTO(
                fajr=day.fajr.strftime("%H:%M"),
                syuruq=day.syuruq.strftime("%H:%M"),
                dhuhr=day.dhuhr.strftime("%H:%M"),
                asr=day.asr.strftime("%H:%M"),
                maghrib=day.maghrib.strftime("%H:%M"),
                isha=day.isha.strftime("%H:%M"),
            ),
            source=day.source,
            stale=stale,
        )


class NextEventDTO(ContractDTO):
    state: StateLiteral
    now: datetime
    next_prayer: PrayerLiteral | None
    adhan_at: datetime | None
    iqamah_at: datetime | None
    dim_until: datetime | None
    stale: bool

    @classmethod
    def from_domain(cls, event: NextEvent) -> NextEventDTO:
        return cls(
            state=event.state.name,
            now=event.now,
            next_prayer=event.next_prayer.value if event.next_prayer else None,
            adhan_at=event.adhan_at,
            iqamah_at=event.iqamah_at,
            dim_until=event.dim_until,
            stale=event.stale,
        )
