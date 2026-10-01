"""Playlist window grammar: single owner for clock/marker bounds.

The window language is ``HH:MM`` clock times or marker names with an
offset in minutes applied, ``None`` staying open, and inverted bounds
spanning midnight. Anchor mode (``anchor_marker`` plus both offsets)
ignores the clock bounds. Boundary markers cannot anchor a playlist;
clock bounds may name any marker (matching the previous write and
resolve behavior).

Pure over pinned inputs: no wall-clock reads, deterministic for the
same ``(playlist, day, now)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from muhideen.core.values import (
    MarkerKind,
    MarkerName,
    Playlist,
    PrayerDay,
    marker_kind,
)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
"""Clock-window bound shape; anything else must name a time marker."""


@dataclass(frozen=True, slots=True)
class WindowBound:
    """One typed window bound: a clock time or a marker name."""

    kind: Literal["clock", "marker"]
    clock: time | None = None
    marker: MarkerName | None = None


@dataclass(frozen=True, slots=True)
class TypedWindow:
    """Parsed playlist window: symbolic bounds plus offsets."""

    start: WindowBound | None
    end: WindowBound | None
    anchor: MarkerName | None
    start_offset_min: int = 0
    stop_offset_min: int = 0


def parse_bound(raw: str | None) -> WindowBound | None:
    """Parse one raw bound; ``None`` stays open.

    Raises ``ValueError`` when the bound is neither ``HH:MM`` nor a
    marker name.
    """
    if raw is None:
        return None
    candidate = raw.strip()
    if _HHMM_RE.match(candidate):
        hour_raw, minute_raw = candidate.split(":")
        return WindowBound(kind="clock", clock=time(int(hour_raw), int(minute_raw)))
    try:
        marker = MarkerName(candidate.lower())
    except ValueError:
        raise ValueError(
            f"playlist window bound is not HH:MM or marker: {raw!r}"
        ) from None
    return WindowBound(kind="marker", marker=marker)


def parse_window(playlist: Playlist) -> TypedWindow:
    """Parse one playlist's window into its typed form.

    Raises ``ValueError`` for boundary anchors and for bounds that are
    neither ``HH:MM`` nor a marker name.
    """
    if (
        playlist.anchor_marker is not None
        and marker_kind(playlist.anchor_marker) is MarkerKind.BOUNDARY
    ):
        raise ValueError(
            "boundary marker cannot anchor a playlist: "
            f"{playlist.anchor_marker.value!r}"
        )
    try:
        start = parse_bound(playlist.window_start)
    except ValueError as exc:
        raise ValueError(f"window_start {exc}") from None
    try:
        end = parse_bound(playlist.window_end)
    except ValueError as exc:
        raise ValueError(f"window_end {exc}") from None
    return TypedWindow(
        start=start,
        end=end,
        anchor=playlist.anchor_marker,
        start_offset_min=playlist.anchor_start_offset_min,
        stop_offset_min=playlist.anchor_stop_offset_min,
    )


def _marker_dt(day: PrayerDay, marker: MarkerName, now: datetime) -> datetime:
    """One marker's instant, anchored on ``now``'s date."""
    slot = day.dhuhr if marker is MarkerName.JUMUAH else getattr(day, marker.value)
    return datetime.combine(now.date(), slot, tzinfo=now.tzinfo)


def window_datetimes(
    typed: TypedWindow, day: PrayerDay, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """Active datetimes for one typed window; ``None`` bounds stay open."""
    if typed.anchor is not None:
        base = _marker_dt(day, typed.anchor, now)
        return (
            base + timedelta(minutes=typed.start_offset_min),
            base + timedelta(minutes=typed.stop_offset_min),
        )
    start: datetime | None = None
    stop: datetime | None = None
    if typed.start is not None:
        if typed.start.kind == "marker" and typed.start.marker is not None:
            start = _marker_dt(day, typed.start.marker, now) + timedelta(
                minutes=typed.start_offset_min
            )
        elif typed.start.clock is not None:
            start = datetime.combine(now.date(), typed.start.clock, tzinfo=now.tzinfo)
    if typed.end is not None:
        if typed.end.kind == "marker" and typed.end.marker is not None:
            stop = _marker_dt(day, typed.end.marker, now) + timedelta(
                minutes=typed.stop_offset_min
            )
        elif typed.end.clock is not None:
            stop = datetime.combine(now.date(), typed.end.clock, tzinfo=now.tzinfo)
    return start, stop


def is_in_window(start: datetime | None, stop: datetime | None, now: datetime) -> bool:
    """Half-open ``[start, stop)`` membership; inverted bounds span midnight."""
    if start is not None and stop is not None and stop <= start:
        return now >= start or now < stop
    if start is not None and now < start:
        return False
    return stop is None or now < stop
