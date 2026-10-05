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

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    """Display overlay state driven by Prayer Time Markers only."""

    NORMAL = "normal"
    PRE_ADHAN = "pre_adhan"
    ADHAN = "adhan"
    IQAMAH_COUNTDOWN = "iqamah_countdown"
    SALAH_DIM = "salah_dim"


class ScheduleSource(StrEnum):
    """Source of a prayer day: JAKIM, Aladhan-compatible API, CALC, or MANUAL."""

    JAKIM = "jakim"
    ALADHAN = "aladhan"
    CALC = "calc"
    MANUAL = "manual"


SyncProvider = Literal["jakim", "aladhan", "none"]
"""Sync source choice: a REST provider, or explicit offline (no fetch ever)."""


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
    time_synced: bool = True


@dataclass(frozen=True, slots=True)
class IqamahRule:
    """One prayer-only iqamah rule: delay minutes or a fixed clock time."""

    prayer: MarkerName
    mode: Literal["delay", "fixed"]
    delay_minutes: int = 10
    fixed_time: time | None = None

    def __post_init__(self) -> None:
        """Reject out-of-range delays: negative inverts the state machine."""
        if not 0 <= self.delay_minutes <= 60:
            raise ValueError(f"delay_minutes out of range 0-60: {self.delay_minutes}")


DEFAULT_IQAMAH_RULES: tuple[IqamahRule, ...] = (
    IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.DHUHR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.ASR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.MAGHRIB, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.ISHA, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=10),
)
"""PRD FR-1.4 iqamah defaults: Fajr 15, Dhuhr/Asr/Maghrib 10, Isha 15,
Jumuah its own rule. Boundary Time Markers have no iqamah, so no rule."""


ThemePalette = Literal["classic-green", "midnight", "sand"]
"""Closed palette enum: default green, dark midnight, warm sand."""

ThemeFont = Literal["outfit", "system"]
"""Closed font enum: vendored Outfit or the offline system stack."""

ThemeCountdownStyle = Literal["boxes", "inline"]
"""Closed countdown enum: H/M/S boxes or a single inline line."""

ThemeClockFormat = Literal["24h", "24h-seconds", "12h"]
"""Closed clock enum: hours+minutes with or without seconds, 12h AM/PM."""

ThemeHijriForm = Literal["long", "short"]
"""Closed Hijri enum: long month name or the raw wire date."""

ThemeBoundaryStrip = Literal["show", "hide"]
"""Closed boundary-strip enum: render the Imsak/Syuruq/Dhuha strip or not."""

ThemeDensity = Literal["comfortable", "compact"]
"""Closed density enum: default spacing or a compact variant."""


AsrJuristic = Literal["shafi", "hanafi"]
"""Asr juristic setting: Standard (Shafi, shadow factor 1) or Hanafi (factor 2)."""


TIMEZONE_DEFAULT = "Asia/Kuala_Lumpur"
"""Single owner of the device-clock timezone default (existing installs stay KL)."""


THEME_DEFAULTS: dict[str, str] = {
    "palette": "classic-green",
    "font": "outfit",
    "countdown_style": "boxes",
    "clock_format": "12h",
    "hijri_form": "long",
    "boundary_strip": "show",
    "density": "comfortable",
}
"""Single owner of theme-knob defaults; wire/rows/script read from here."""

THEME_CHOICES: dict[str, tuple[str, ...]] = {
    "palette": ("classic-green", "midnight", "sand"),
    "font": ("outfit", "system"),
    "countdown_style": ("boxes", "inline"),
    "clock_format": ("24h", "24h-seconds", "12h"),
    "hijri_form": ("long", "short"),
    "boundary_strip": ("show", "hide"),
    "density": ("comfortable", "compact"),
}
"""Single owner of closed-enum rules; guards read from here."""

THEME_KV_KEYS: tuple[str, ...] = (
    "theme.palette",
    "theme.font",
    "theme.countdown_style",
    "theme.clock_format",
    "theme.hijri_form",
    "theme.boundary_strip",
    "theme.density",
)
"""Stored-row keys for the seven theme knobs."""


@dataclass(frozen=True, slots=True)
class ThemeSettings:
    """Closed-enum display knobs: palette, font, and layout variants.

    Every field is a closed enum — unknown values raise ``ValueError`` so
    the repo boundary and the DTO layer both surface them as 422/ConfigError
    instead of rendering an undefined variant.
    """

    palette: ThemePalette = cast(ThemePalette, THEME_DEFAULTS["palette"])
    font: ThemeFont = cast(ThemeFont, THEME_DEFAULTS["font"])
    countdown_style: ThemeCountdownStyle = cast(
        ThemeCountdownStyle, THEME_DEFAULTS["countdown_style"]
    )
    clock_format: ThemeClockFormat = cast(
        ThemeClockFormat, THEME_DEFAULTS["clock_format"]
    )
    hijri_form: ThemeHijriForm = cast(ThemeHijriForm, THEME_DEFAULTS["hijri_form"])
    boundary_strip: ThemeBoundaryStrip = cast(
        ThemeBoundaryStrip, THEME_DEFAULTS["boundary_strip"]
    )
    density: ThemeDensity = cast(ThemeDensity, THEME_DEFAULTS["density"])

    def __post_init__(self) -> None:
        """Reject any knob value outside its closed enum."""
        for knob, choices in THEME_CHOICES.items():
            value = getattr(self, knob)
            if value not in choices:
                raise ValueError(
                    f"{knob} must be one of {', '.join(choices)}: {value!r}"
                )


def theme_default(knob: str) -> str:
    """Default value for one theme knob from the single owner table."""
    return THEME_DEFAULTS[knob]


def theme_choices(knob: str) -> tuple[str, ...]:
    """Closed choices for one theme knob from the single owner table."""
    return THEME_CHOICES[knob]


def theme_pairs(theme: ThemeSettings) -> list[tuple[str, str]]:
    """Render one ThemeSettings as its seven ``theme.*`` settings rows."""
    return [(f"theme.{knob}", str(getattr(theme, knob))) for knob in THEME_DEFAULTS]


def theme_from_kv(kv: dict[str, str]) -> ThemeSettings:
    """Build ThemeSettings from settings rows; missing keys take defaults."""
    return ThemeSettings(
        palette=cast(ThemePalette, kv.get("theme.palette", THEME_DEFAULTS["palette"])),
        font=cast(ThemeFont, kv.get("theme.font", THEME_DEFAULTS["font"])),
        countdown_style=cast(
            ThemeCountdownStyle,
            kv.get("theme.countdown_style", THEME_DEFAULTS["countdown_style"]),
        ),
        clock_format=cast(
            ThemeClockFormat,
            kv.get("theme.clock_format", THEME_DEFAULTS["clock_format"]),
        ),
        hijri_form=cast(
            ThemeHijriForm, kv.get("theme.hijri_form", THEME_DEFAULTS["hijri_form"])
        ),
        boundary_strip=cast(
            ThemeBoundaryStrip,
            kv.get("theme.boundary_strip", THEME_DEFAULTS["boundary_strip"]),
        ),
        density=cast(ThemeDensity, kv.get("theme.density", THEME_DEFAULTS["density"])),
    )


def theme_css_class(theme: ThemeSettings) -> str:
    """Presentation class for one theme: palette, font, and density."""
    return f"palette-{theme.palette} font-{theme.font} density-{theme.density}"


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
"""Quiet-hours bound shape: 24h ``HH:MM`` (same wire shape as prayer times)."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Installation identity, display tuning, and schedule-source switches."""

    masjid_name: str
    zone: str
    hijri_offset: int
    jakim_zone: str | None = None
    sync_provider: SyncProvider = "jakim"
    adhan_duration_s: int = 180
    dim_minutes_default: int = 20
    dim_minutes_jumuah: int = 45
    iqamah_rules: tuple[IqamahRule, ...] = DEFAULT_IQAMAH_RULES
    lat: float | None = None
    lon: float | None = None
    method: str = "MABIMS"
    asr_juristic: AsrJuristic = "shafi"
    boundary_countdown: bool = False
    calc_only: bool = False
    imsak_offset_min: int = 10
    dhuha_offset_min: int = 28
    countdown_before_adhan_min: int = 5
    countdown_before_adhan_overrides: dict[str, int] = field(
        default_factory=dict[str, int], hash=False
    )
    theme: ThemeSettings = field(default_factory=ThemeSettings)
    timezone: str = TIMEZONE_DEFAULT
    adhan_audio_enabled: bool = False
    adhan_volume: int = 70
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    adhan_muted_prayers: list[str] = field(default_factory=list[str], hash=False)

    def __post_init__(self) -> None:
        """Enforce offset/coordinate guards and non-empty rule coverage."""
        if not -2 <= self.hijri_offset <= 2:
            raise ValueError(f"hijri_offset out of range: {self.hijri_offset}")
        if not self.adhan_duration_s > 0:
            raise ValueError(
                f"adhan_duration_s must be positive: {self.adhan_duration_s}"
            )
        if not 5 <= self.dim_minutes_default <= 60:
            raise ValueError(
                f"dim_minutes_default out of range 5-60: {self.dim_minutes_default}"
            )
        if not 5 <= self.dim_minutes_jumuah <= 60:
            raise ValueError(
                f"dim_minutes_jumuah out of range 5-60: {self.dim_minutes_jumuah}"
            )
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
            raise ValueError(f"unknown timezone: {self.timezone!r}") from exc
        if self.asr_juristic not in ("shafi", "hanafi"):
            raise ValueError(f"unknown asr juristic setting: {self.asr_juristic!r}")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        if self.lat is not None and not -90 <= self.lat <= 90:
            raise ValueError(f"latitude out of range: {self.lat}")
        if self.lon is not None and not -180 <= self.lon <= 180:
            raise ValueError(f"longitude out of range: {self.lon}")
        if not 0 <= self.imsak_offset_min <= 10:
            raise ValueError(f"imsak_offset_min out of range: {self.imsak_offset_min}")
        if not 15 <= self.dhuha_offset_min <= 30:
            raise ValueError(f"dhuha_offset_min out of range: {self.dhuha_offset_min}")
        if not 0 <= self.countdown_before_adhan_min <= 90:
            raise ValueError(
                "countdown_before_adhan_min out of range: "
                f"{self.countdown_before_adhan_min}"
            )
        for prayer_key, minutes in self.countdown_before_adhan_overrides.items():
            try:
                marker = MarkerName(prayer_key)
            except ValueError:
                raise ValueError(
                    f"unknown prayer for countdown override: {prayer_key!r}"
                ) from None
            if marker_kind(marker) is MarkerKind.BOUNDARY:
                raise ValueError(
                    "boundary time marker cannot have a countdown override: "
                    f"{prayer_key}"
                )
            if not 0 <= minutes <= 90:
                raise ValueError(
                    f"countdown override out of range for {prayer_key}: {minutes}"
                )
        if not 0 <= self.adhan_volume <= 100:
            raise ValueError(f"adhan_volume out of range 0-100: {self.adhan_volume}")
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours need both start and end")
        for bound in (self.quiet_hours_start, self.quiet_hours_end):
            if bound is not None and not _HHMM.match(bound):
                raise ValueError(f"quiet hours must be HH:MM: {bound!r}")
        for prayer_key in self.adhan_muted_prayers:
            try:
                marker = MarkerName(prayer_key)
            except ValueError:
                raise ValueError(
                    f"unknown prayer for adhan mute: {prayer_key!r}"
                ) from None
            if marker_kind(marker) is MarkerKind.BOUNDARY:
                raise ValueError(
                    f"boundary time marker cannot mute adhan: {prayer_key}"
                )
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


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    """One image slot in a playlist: path, on-stage seconds, display order."""

    image_path: str
    duration_s: int
    sort_order: int = 0

    def __post_init__(self) -> None:
        """Enforce non-empty path, positive duration, non-negative order."""
        if not self.image_path:
            raise ValueError("playlist item needs an image path")
        if self.duration_s <= 0:
            raise ValueError(
                f"playlist item duration must be positive: {self.duration_s}"
            )
        if self.sort_order < 0:
            raise ValueError(
                f"playlist item sort order cannot be negative: {self.sort_order}"
            )


CycleMode = Literal["indefinite", "repeat"]
"""Playlist cycling policy: loop forever or release after ``max_cycles`` passes."""


@dataclass(frozen=True, slots=True)
class Playlist:
    """A named set of image items with a schedule and a cycling policy.

    The window is either clock-based (``window_start``/``window_end`` as
    ``HH:MM`` strings, each optionally a marker name with its offset
    applied) or anchored to one marker (``anchor_marker`` plus both
    offsets, ignoring the clock bounds). ``None`` bounds stay open, so a
    playlist with no bounds at all is always in-window while active.
    Only image items are supported for now.
    """

    id: str
    title: str
    active: bool
    window_start: str | None = None
    window_end: str | None = None
    anchor_marker: MarkerName | None = None
    anchor_start_offset_min: int = 0
    anchor_stop_offset_min: int = 0
    cycle_mode: CycleMode = "indefinite"
    max_cycles: int | None = None
    items: tuple[PlaylistItem, ...] = ()

    def __post_init__(self) -> None:
        """Enforce non-empty identity plus the cycle-mode/max_cycles pairing."""
        if not self.id:
            raise ValueError("playlist needs a non-empty id")
        if not self.title:
            raise ValueError("playlist needs a non-empty title")
        if self.cycle_mode == "repeat":
            if self.max_cycles is None or self.max_cycles < 1:
                raise ValueError(
                    f"repeat playlists need max_cycles >= 1: {self.max_cycles!r}"
                )
        elif self.cycle_mode == "indefinite":
            if self.max_cycles is not None:
                raise ValueError(
                    f"indefinite playlists need max_cycles None: {self.max_cycles!r}"
                )
        else:
            raise ValueError(f"unknown playlist cycle mode: {self.cycle_mode!r}")
