"""Pydantic models for config/muhideen.json (replaces SettingsDTO + display tables)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from muhideen.core.values import (
    ThemeBoundaryStrip,
    ThemeClockFormat,
    ThemeCountdownStyle,
    ThemeDensity,
    ThemeFont,
    ThemeHijriForm,
    ThemePalette,
)

TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
"""File-local HH:MM shape (same regex as api.dto to avoid adapters->api import)."""

IqamahPrayer = Literal["fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"]
"""Prayer Time Markers that may carry an iqamah rule (no boundary markers)."""

IqamahMode = Literal["delay", "fixed"]
"""Iqamah policy: minutes after adhan, or a fixed clock time."""

REQUIRED_IQAMAH_PRAYERS: tuple[IqamahPrayer, ...] = (
    "fajr",
    "dhuhr",
    "asr",
    "maghrib",
    "isha",
    "jumuah",
)
"""Every Prayer Time Marker needs exactly one iqamah rule (FR-1.4)."""

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
    manual_days: list[ManualDay] = Field(default_factory=list[ManualDay])

    @model_validator(mode="after")
    def _lat_lon_paired(self) -> Schedule:
        """Coordinates are paired-nullable: both set or both absent."""
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        return self


class IqamahRuleFile(Strict):
    """One prayer-only iqamah rule: delay minutes or a fixed clock time."""

    prayer: IqamahPrayer
    mode: IqamahMode
    delay_minutes: Annotated[int, Field(ge=0, le=60)] = 10
    fixed_time: TimeHHMM | None = None

    @model_validator(mode="after")
    def _fixed_needs_time(self) -> IqamahRuleFile:
        """A fixed-mode rule without a clock time can never resolve."""
        if self.mode == "fixed" and self.fixed_time is None:
            raise ValueError(f"fixed iqamah rule without time: {self.prayer}")
        return self


class Timing(Strict):
    """Adhan/dim/countdown knobs plus exactly six prayer-only iqamah rules."""

    adhan_duration_s: Annotated[int, Field(gt=0)] = 180
    dim_minutes_default: Annotated[int, Field(ge=5, le=60)] = 20
    dim_minutes_jumuah: Annotated[int, Field(ge=5, le=60)] = 45
    countdown_before_adhan_min: Annotated[int, Field(ge=0, le=90)] = 5
    countdown_before_adhan_overrides: dict[str, Annotated[int, Field(ge=0, le=90)]] = (
        Field(default_factory=dict)
    )
    iqamah_rules: Annotated[list[IqamahRuleFile], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def _rules_cover_prayers_exactly(self) -> Timing:
        """Six slots must cover every Prayer Time Marker exactly once."""
        prayers = [rule.prayer for rule in self.iqamah_rules]
        if len(set(prayers)) != len(prayers):
            dupes = sorted({p for p in prayers if prayers.count(p) > 1})
            raise ValueError(f"duplicate iqamah rule for prayer: {dupes}")
        missing = [p for p in REQUIRED_IQAMAH_PRAYERS if p not in prayers]
        if missing:
            raise ValueError(f"missing iqamah rule for prayer: {missing}")
        return self


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

    palette: ThemePalette = "classic-green"
    font: ThemeFont = "outfit"
    countdown_style: ThemeCountdownStyle = "boxes"
    clock_format: ThemeClockFormat = "12h"
    hijri_form: ThemeHijriForm = "long"
    boundary_strip: ThemeBoundaryStrip = "show"
    density: ThemeDensity = "comfortable"


class DisplayTheme(Strict):
    """Per-display partial theme: any subset of the seven knobs, rest inherited."""

    palette: ThemePalette | None = None
    font: ThemeFont | None = None
    countdown_style: ThemeCountdownStyle | None = None
    clock_format: ThemeClockFormat | None = None
    hijri_form: ThemeHijriForm | None = None
    boundary_strip: ThemeBoundaryStrip | None = None
    density: ThemeDensity | None = None

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


class PlaylistItemFile(Strict):
    """One image slot in a playlist: path, on-stage seconds, display order."""

    image_path: Annotated[str, Field(min_length=1)]
    duration_s: Annotated[int, Field(gt=0)]
    sort_order: Annotated[int, Field(ge=0)] = 0


class PlaylistFile(Strict):
    """A named set of image items with a schedule window and cycling policy."""

    id: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    active: bool = True
    window_start: str | None = None
    window_end: str | None = None
    anchor_marker: str | None = None
    anchor_start_offset_min: int = 0
    anchor_stop_offset_min: int = 0
    cycle_mode: Literal["indefinite", "repeat"] = "indefinite"
    max_cycles: int | None = None
    items: list[PlaylistItemFile] = Field(default_factory=list[PlaylistItemFile])

    @model_validator(mode="after")
    def _cycle_pairing(self) -> PlaylistFile:
        """Mirror core Playlist: repeat needs max_cycles>=1, indefinite None."""
        if self.cycle_mode == "repeat":
            if self.max_cycles is None or self.max_cycles < 1:
                raise ValueError(
                    f"repeat playlists need max_cycles >= 1: {self.max_cycles!r}"
                )
        elif self.max_cycles is not None:
            raise ValueError(
                f"indefinite playlists need max_cycles None: {self.max_cycles!r}"
            )
        return self

    @model_validator(mode="after")
    def _items_capped(self) -> PlaylistFile:
        """Cap items per playlist so one file entry cannot bloat the carousel."""
        if len(self.items) > 50:
            raise ValueError(f"playlist items capped at 50: {len(self.items)}")
        return self


def _default_timing() -> Timing:
    """Timing defaults with the FR-1.4 iqamah set (mirrors core defaults)."""
    delays: tuple[tuple[IqamahPrayer, int], ...] = (
        ("fajr", 15),
        ("dhuhr", 10),
        ("asr", 10),
        ("maghrib", 10),
        ("isha", 15),
        ("jumuah", 10),
    )
    return Timing(
        iqamah_rules=[
            IqamahRuleFile(prayer=prayer, mode="delay", delay_minutes=minutes)
            for prayer, minutes in delays
        ]
    )


class ConfigFile(Strict):
    """Root of config/muhideen.json: identity, schedule, timing, theme, displays."""

    schema_version: Annotated[int, Field(alias="$schemaVersion")] = 1
    masjid: Masjid
    schedule: Schedule = Field(default_factory=Schedule)
    timing: Timing = Field(default_factory=_default_timing)
    adhan_audio: AdhanAudio = Field(default_factory=AdhanAudio)
    theme: Theme = Field(default_factory=Theme)
    displays: Annotated[dict[str, Display], Field(min_length=1)]
    playlists: list[PlaylistFile] = Field(default_factory=list[PlaylistFile])
