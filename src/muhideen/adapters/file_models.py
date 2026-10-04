"""Pydantic models for config/muhideen.json (replaces SettingsDTO + display tables)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from muhideen.api.dto import IqamahRuleDTO, TimeHHMM

Palette = Literal["classic-green", "midnight", "sand"]
Language = Literal["en", "ms", "ar"]


class Strict(BaseModel):
    """Shared file-model rules: reject unknown keys, allow alias or field name."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Masjid(Strict):
    """Installation identity: display name, JAKIM zone code, IANA timezone."""

    name: Annotated[str, Field(min_length=1, max_length=200)]
    zone: Annotated[str, Field(min_length=1, max_length=32)]
    timezone: str = "Asia/Kuala_Lumpur"


class ManualDay(Strict):
    """One hand-pinned day: date plus the eight day-local HH:MM markers."""

    date: date
    imsak: TimeHHMM
    fajr: TimeHHMM
    syuruq: TimeHHMM
    dhuha: TimeHHMM
    dhuhr: TimeHHMM
    asr: TimeHHMM
    maghrib: TimeHHMM
    isha: TimeHHMM


class Schedule(Strict):
    """Schedule-source switches plus boundary offsets and manual pins."""

    method: Literal["MABIMS", "MWL", "ISNA", "Egyptian"] = "MABIMS"
    asr_juristic: Literal["shafi", "hanafi"] = "shafi"
    lat: Annotated[float | None, Field(ge=-90, le=90)] = None
    lon: Annotated[float | None, Field(ge=-180, le=180)] = None
    calc_only: bool = False
    hijri_offset: Annotated[int, Field(ge=-2, le=2)] = 0
    imsak_offset_min: Annotated[int, Field(ge=0, le=10)] = 10
    dhuha_offset_min: Annotated[int, Field(ge=15, le=30)] = 28
    boundary_countdown: bool = False
    manual_days: list[ManualDay] = Field(default_factory=list)

    @model_validator(mode="after")
    def _lat_lon_paired(self) -> Schedule:
        """Coordinates are paired-nullable: both set or both absent."""
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        return self


class Timing(Strict):
    """Adhan/dim/countdown knobs plus exactly six prayer-only iqamah rules."""

    adhan_duration_s: Annotated[int, Field(gt=0)] = 180
    dim_minutes_default: Annotated[int, Field(ge=5, le=60)] = 20
    dim_minutes_jumuah: Annotated[int, Field(ge=5, le=60)] = 45
    countdown_before_adhan_min: Annotated[int, Field(ge=0, le=90)] = 5
    countdown_before_adhan_overrides: dict[
        str, Annotated[int, Field(ge=0, le=90)]
    ] = Field(default_factory=dict)
    iqamah_rules: Annotated[list[IqamahRuleDTO], Field(min_length=6, max_length=6)]


class AdhanAudio(Strict):
    """Adhan playback knobs: master switch, volume, quiet hours, mutes, file."""

    enabled: bool = False
    volume: Annotated[int, Field(ge=0, le=100)] = 70
    quiet_hours_start: TimeHHMM | None = None
    quiet_hours_end: TimeHHMM | None = None
    muted_prayers: list[str] = Field(default_factory=list)
    file: str = "media/adhan.mp3"

    @model_validator(mode="after")
    def _quiet_hours_paired(self) -> AdhanAudio:
        """Quiet hours need both bounds or neither."""
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours need both start and end")
        return self


class Theme(Strict):
    """Global display defaults: seven closed enums (see core/values.py)."""

    palette: Palette = "classic-green"
    font: Literal["outfit", "system"] = "outfit"
    countdown_style: Literal["boxes", "inline"] = "boxes"
    clock_format: Literal["24h", "24h-seconds", "12h"] = "12h"
    hijri_form: Literal["long", "short"] = "long"
    boundary_strip: Literal["show", "hide"] = "show"
    density: Literal["comfortable", "compact"] = "comfortable"


class DisplayTheme(Strict):
    """Per-display partial theme: any subset of the seven knobs, rest inherited."""

    palette: Palette | None = None
    font: Literal["outfit", "system"] | None = None
    countdown_style: Literal["boxes", "inline"] | None = None
    clock_format: Literal["24h", "24h-seconds", "12h"] | None = None
    hijri_form: Literal["long", "short"] | None = None
    boundary_strip: Literal["show", "hide"] | None = None
    density: Literal["comfortable", "compact"] | None = None

    def __getitem__(self, key: str) -> Any:
        """Allow dict-style reads (``theme["palette"]``) alongside attributes."""
        if key not in type(self).model_fields:
            raise KeyError(key)
        return getattr(self, key)


class Display(Strict):
    """One display entry: language, theme overlay, dim/carousel/color overrides."""

    name: str | None = None
    language: Language = "en"
    theme: DisplayTheme = Field(default_factory=DisplayTheme)
    dim_minutes_override: Annotated[int | None, Field(ge=5, le=60)] = None
    carousel_enabled: bool = True
    custom_colors: dict[str, str] | None = None


class ConfigFile(Strict):
    """Root of config/muhideen.json: identity, schedule, timing, theme, displays."""

    schema_version: Annotated[int, Field(alias="$schemaVersion")] = 1
    masjid: Masjid
    schedule: Schedule = Field(default_factory=Schedule)
    timing: Timing = Field(default_factory=Timing)
    adhan_audio: AdhanAudio = Field(default_factory=AdhanAudio)
    theme: Theme = Field(default_factory=Theme)
    displays: Annotated[dict[str, Display], Field(min_length=1)]
    playlists: list[dict] = Field(default_factory=list)
