"""Pydantic DTOs: the executable API contract (slice 1A-3).

Wire formats follow ``docs/api-contract.md`` exactly: uppercase ``state``
values, ``HH:MM`` prayer times, tz-aware ISO8601 datetimes, and
``extra="forbid"`` everywhere so any drift fails validation. Mappers convert
from ``muhideen.core`` value objects; this module never reads the clock and
performs no business logic (that is ``domain/``'s job).

Marker taxonomy (PRD Rev 3): two classes on the wire. ``prayers`` holds the
five Prayer Time Markers (the only markers with an adhan/iqamah/dim
lifecycle); ``boundaries`` holds Imsak/Syuruq/Dhuha — informational, never
non-NORMAL. ``next_prayer`` is therefore narrowed to Prayer Time Markers,
while ``next_boundary``/``boundary_at`` carry the opt-in boundary countdown
pointer (``Settings.boundary_countdown``, default off).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from muhideen.core.values import (
    NextEvent,
    PrayerDay,
    ScheduleSource,
)

StateLiteral = Literal["NORMAL", "PRE_ADHAN", "ADHAN", "IQAMAH_COUNTDOWN", "SALAH_DIM"]
PrayerLiteral = Literal["fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"]
BoundaryLiteral = Literal["imsak", "syuruq", "dhuha"]
TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

__all__ = [
    "BoundaryLiteral",
    "BoundaryTimesDTO",
    "ConfigUpdateEventDTO",
    "ContractDTO",
    "NextEventDTO",
    "PrayerDayDTO",
    "PrayerLiteral",
    "PrayerTimesDTO",
    "SSE_PAYLOAD_MODELS",
    "StateEventDTO",
    "StateLiteral",
    "TickEventDTO",
    "TimeHHMM",
    "VersionDTO",
]


class ContractDTO(BaseModel):
    """Shared wire rules: no extra fields, tz-aware datetimes only."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _reject_naive_datetimes(cls, value: object) -> object:
        """Reject naive datetimes on every DTO field (contract rule)."""
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("datetime fields must be tz-aware (ISO8601 offset)")
        return value


class PrayerTimesDTO(ContractDTO):
    """The 5 Prayer Time Markers — cards/hero level only."""

    fajr: TimeHHMM
    dhuhr: TimeHHMM
    asr: TimeHHMM
    maghrib: TimeHHMM
    isha: TimeHHMM


class BoundaryTimesDTO(ContractDTO):
    """The 3 Boundary Time Markers — secondary level, never adhan/iqamah/dim."""

    imsak: TimeHHMM
    syuruq: TimeHHMM
    dhuha: TimeHHMM


class PrayerDayDTO(ContractDTO):
    """GET /api/prayer-day payload: one day split into prayers+boundaries."""

    date: date
    zone: str
    prayers: PrayerTimesDTO
    boundaries: BoundaryTimesDTO
    source: ScheduleSource
    stale: bool
    hijri_date: str | None

    @classmethod
    def from_domain(
        cls, day: PrayerDay, stale: bool, *, hijri_date: str | None = None
    ) -> PrayerDayDTO:
        """Map a PrayerDay to HH:MM wire groups plus the staleness flag."""
        return cls(
            date=day.date,
            zone=day.zone,
            prayers=PrayerTimesDTO(
                fajr=day.fajr.strftime("%H:%M"),
                dhuhr=day.dhuhr.strftime("%H:%M"),
                asr=day.asr.strftime("%H:%M"),
                maghrib=day.maghrib.strftime("%H:%M"),
                isha=day.isha.strftime("%H:%M"),
            ),
            boundaries=BoundaryTimesDTO(
                imsak=day.imsak.strftime("%H:%M"),
                syuruq=day.syuruq.strftime("%H:%M"),
                dhuha=day.dhuha.strftime("%H:%M"),
            ),
            source=day.source,
            stale=stale,
            hijri_date=hijri_date,
        )


class NextEventDTO(ContractDTO):
    """GET /api/next-event payload: state plus prayer/boundary pointers."""

    state: StateLiteral
    now: datetime
    next_prayer: PrayerLiteral | None
    adhan_at: datetime | None
    iqamah_at: datetime | None
    dim_until: datetime | None
    stale: bool
    next_boundary: BoundaryLiteral | None
    boundary_at: datetime | None
    time_synced: bool

    @classmethod
    def from_domain(cls, event: NextEvent) -> NextEventDTO:
        """Map a NextEvent, narrowing markers to their wire literals."""
        return cls(
            state=event.state.name,
            now=event.now,
            # Domain guarantees a Prayer Time Marker here (PRD FR-1.7);
            # PrayerLiteral is the explicit wire narrowing to that class.
            next_prayer=cast(PrayerLiteral, event.next_prayer.value)
            if event.next_prayer
            else None,
            adhan_at=event.adhan_at,
            iqamah_at=event.iqamah_at,
            dim_until=event.dim_until,
            stale=event.stale,
            next_boundary=(
                cast(BoundaryLiteral, event.next_boundary.value)
                if event.next_boundary
                else None
            ),
            boundary_at=event.boundary_at,
            time_synced=event.time_synced,
        )


class StateEventDTO(ContractDTO):
    """SSE `state` event payload; absent targets omitted via exclude_none.

    `time_synced` (FR-1.6) is required and never omitted — the
    `TIME UNSYNCED` banner must survive every state transition.
    """

    state: StateLiteral
    time_synced: bool
    now: datetime | None = None
    next_prayer: PrayerLiteral | None = None
    adhan_at: datetime | None = None
    iqamah_at: datetime | None = None
    dim_until: datetime | None = None
    stale: bool | None = None
    next_boundary: BoundaryLiteral | None = None
    boundary_at: datetime | None = None

    @classmethod
    def from_domain(cls, event: NextEvent) -> StateEventDTO:
        """Map a NextEvent to the state frame (None fields omitted on dump)."""
        return cls(
            state=event.state.name,
            time_synced=event.time_synced,
            now=event.now,
            next_prayer=cast(PrayerLiteral, event.next_prayer.value)
            if event.next_prayer
            else None,
            adhan_at=event.adhan_at,
            iqamah_at=event.iqamah_at,
            dim_until=event.dim_until,
            stale=event.stale,
            next_boundary=(
                cast(BoundaryLiteral, event.next_boundary.value)
                if event.next_boundary
                else None
            ),
            boundary_at=event.boundary_at,
        )


class TickEventDTO(ContractDTO):
    """SSE `tick` event payload: server `now` + state + Stage id, sent 1/min."""

    now: datetime
    state: StateLiteral
    stage: str

    @classmethod
    def from_domain(cls, event: NextEvent, stage: str) -> TickEventDTO:
        """Map a NextEvent plus its Stage id to the per-minute tick frame."""
        return cls(now=event.now, state=event.state.name, stage=stage)


class ConfigUpdateEventDTO(ContractDTO):
    """SSE `config-update` event payload: which config groups changed."""

    changed: Annotated[list[str], Field(min_length=1)]


SSE_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "state": StateEventDTO,
    "tick": TickEventDTO,
    "config-update": ConfigUpdateEventDTO,
}


class VersionDTO(ContractDTO):
    """GET /api/version payload; `api` literal pins the contract generation."""

    version: str
    api: Literal["v1"]
