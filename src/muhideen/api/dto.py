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

from datetime import date, datetime, time
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from muhideen.core.values import (
    IqamahRule,
    MarkerName,
    NextEvent,
    PrayerDay,
    ScheduleSource,
    Settings,
)

StateLiteral = Literal["NORMAL", "PRE_ADHAN", "ADHAN", "IQAMAH_COUNTDOWN", "SALAH_DIM"]
PrayerLiteral = Literal["fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"]
BoundaryLiteral = Literal["imsak", "syuruq", "dhuha"]
MethodLiteral = Literal["MABIMS", "MWL", "ISNA", "Egyptian"]
IqamahModeLiteral = Literal["delay", "fixed"]
TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

__all__ = [
    "AuthRequestDTO",
    "AuthResponseDTO",
    "BoundaryLiteral",
    "BoundaryTimesDTO",
    "ConfigUpdateEventDTO",
    "ContractDTO",
    "HeartbeatRequestDTO",
    "HeartbeatResponseDTO",
    "IqamahModeLiteral",
    "IqamahRuleDTO",
    "MethodLiteral",
    "NextEventDTO",
    "PrayerDayDTO",
    "PrayerLiteral",
    "PrayerTimesDTO",
    "SessionStatusDTO",
    "SettingsDTO",
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
    date: date
    zone: str
    prayers: PrayerTimesDTO
    boundaries: BoundaryTimesDTO
    source: ScheduleSource
    stale: bool

    @classmethod
    def from_domain(cls, day: PrayerDay, stale: bool) -> PrayerDayDTO:
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
        )


class NextEventDTO(ContractDTO):
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
    """SSE `tick` event payload: server `now` + state, sent 1/min."""

    now: datetime
    state: StateLiteral

    @classmethod
    def from_domain(cls, event: NextEvent) -> TickEventDTO:
        return cls(now=event.now, state=event.state.name)


class ConfigUpdateEventDTO(ContractDTO):
    """SSE `config-update` event payload: which config groups changed."""

    changed: Annotated[list[str], Field(min_length=1)]


SSE_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "state": StateEventDTO,
    "tick": TickEventDTO,
    "config-update": ConfigUpdateEventDTO,
}


class HeartbeatRequestDTO(ContractDTO):
    """POST /api/displays/heartbeat body: stable pre-registered display ID."""

    id: Annotated[str, Field(min_length=1)]


class HeartbeatResponseDTO(ContractDTO):
    """POST /api/displays/heartbeat response: acknowledgement."""

    ok: bool


class VersionDTO(ContractDTO):
    """GET /api/version payload; `api` literal pins the contract generation."""

    version: str
    api: Literal["v1"]


class IqamahRuleDTO(ContractDTO):
    """One iqamah rule: Prayer Time Marker only, delay or fixed clock time."""

    prayer: PrayerLiteral
    mode: IqamahModeLiteral
    delay_minutes: Annotated[int, Field(ge=0)]
    fixed_time: TimeHHMM | None

    @classmethod
    def from_domain(cls, rule: IqamahRule) -> IqamahRuleDTO:
        return cls(
            prayer=cast(PrayerLiteral, rule.prayer.value),
            mode=rule.mode,
            delay_minutes=rule.delay_minutes,
            fixed_time=(
                rule.fixed_time.strftime("%H:%M")
                if rule.fixed_time is not None
                else None
            ),
        )

    def to_domain(self) -> IqamahRule:
        return IqamahRule(
            prayer=MarkerName(self.prayer),
            mode=self.mode,
            delay_minutes=self.delay_minutes,
            fixed_time=(
                time.fromisoformat(self.fixed_time)
                if self.fixed_time is not None
                else None
            ),
        )


class SettingsDTO(ContractDTO):
    """Full-replace admin settings body and response (all fields required)."""

    masjid_name: Annotated[str, Field(min_length=1, max_length=200)]
    zone: Annotated[str, Field(min_length=1, max_length=32)]
    hijri_offset: Annotated[int, Field(ge=-2, le=2)]
    adhan_duration_s: Annotated[int, Field(gt=0)]
    dim_minutes_default: Annotated[int, Field(ge=5, le=60)]
    dim_minutes_jumuah: Annotated[int, Field(ge=5, le=60)]
    iqamah_rules: Annotated[list[IqamahRuleDTO], Field(min_length=1)]
    lat: Annotated[float | None, Field(ge=-90, le=90)]
    lon: Annotated[float | None, Field(ge=-180, le=180)]
    method: MethodLiteral
    boundary_countdown: bool
    calc_only: bool

    @classmethod
    def from_domain(cls, settings: Settings) -> SettingsDTO:
        return cls(
            masjid_name=settings.masjid_name,
            zone=settings.zone,
            hijri_offset=settings.hijri_offset,
            adhan_duration_s=settings.adhan_duration_s,
            dim_minutes_default=settings.dim_minutes_default,
            dim_minutes_jumuah=settings.dim_minutes_jumuah,
            iqamah_rules=[
                IqamahRuleDTO.from_domain(rule) for rule in settings.iqamah_rules
            ],
            lat=settings.lat,
            lon=settings.lon,
            method=cast(MethodLiteral, settings.method),
            boundary_countdown=settings.boundary_countdown,
            calc_only=settings.calc_only,
        )

    def to_domain(self) -> Settings:
        return Settings(
            masjid_name=self.masjid_name,
            zone=self.zone,
            hijri_offset=self.hijri_offset,
            adhan_duration_s=self.adhan_duration_s,
            dim_minutes_default=self.dim_minutes_default,
            dim_minutes_jumuah=self.dim_minutes_jumuah,
            iqamah_rules=tuple(rule.to_domain() for rule in self.iqamah_rules),
            lat=self.lat,
            lon=self.lon,
            method=self.method,
            boundary_countdown=self.boundary_countdown,
            calc_only=self.calc_only,
        )


class AuthRequestDTO(ContractDTO):
    """Password-only admin credential body for setup and login."""

    password: Annotated[str, Field(min_length=8, max_length=256)]


class AuthResponseDTO(ContractDTO):
    """Auth acknowledgement: setup, login, and logout share this shape."""

    ok: bool


class SessionStatusDTO(ContractDTO):
    """GET /api/auth/session payload: session state plus setup flag."""

    authenticated: bool
    setup_required: bool
