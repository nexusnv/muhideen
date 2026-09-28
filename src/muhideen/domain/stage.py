"""Main Stage occupancy: pure resolution of the hero-area occupier.

Exactly one occupier at a time, ranked Countdown > Playlist > Clock.
Countdowns self-activate in two windows: the pre-adhan takeover ahead of
each adhan and the iqamah window from the end of the adhan overlay to the
iqamah target. Overlapping in-window playlists resolve by most-recent
activation (window start); ties keep input order. The Clock is the default
when nothing else is active.

Pure over pinned inputs: no wall-clock reads, deterministic for the same
``(now, day, settings, event, playlists)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from muhideen.core.errors import ConfigError
from muhideen.core.values import (
    MarkerName,
    NextEvent,
    Playlist,
    PrayerDay,
    Settings,
)
from muhideen.domain.countdown import countdown_window


@dataclass(frozen=True, slots=True)
class ClockOccupant:
    """Default occupier: no countdown active, no playlist in-window."""


@dataclass(frozen=True, slots=True)
class CountdownOccupant:
    """Self-activating occupier: pre-adhan (``adhan``) or post-adhan (``iqamah``)."""

    kind: Literal["adhan", "iqamah"]
    prayer: MarkerName


@dataclass(frozen=True, slots=True)
class PlaylistOccupant:
    """Scheduled occupier: the winning playlist's id."""

    playlist_id: str


StageOccupant = ClockOccupant | CountdownOccupant | PlaylistOccupant
"""The Main Stage occupier: Clock default, Countdown takeover, or Playlist."""


def stage_id(occupant: StageOccupant) -> str:
    """Wire id for one occupier: ``clock``, ``countdown:<kind>:<prayer>``,
    or ``playlist:<id>``."""
    if isinstance(occupant, ClockOccupant):
        return "clock"
    if isinstance(occupant, CountdownOccupant):
        return f"countdown:{occupant.kind}:{occupant.prayer.value}"
    if isinstance(occupant, PlaylistOccupant):
        return f"playlist:{occupant.playlist_id}"
    raise AssertionError(f"unknown stage occupant: {occupant!r}")


def _marker_dt(day: PrayerDay, marker: MarkerName, now: datetime) -> datetime:
    """One marker's instant, anchored on ``now``'s date like the state machine."""
    slot = day.dhuhr if marker is MarkerName.JUMUAH else getattr(day, marker.value)
    return datetime.combine(now.date(), slot, tzinfo=now.tzinfo)


def _parse_bound(raw: str, day: PrayerDay, now: datetime, offset_min: int) -> datetime:
    """One window bound: an ``HH:MM`` clock time or a marker name with its
    offset in minutes applied."""
    candidate = raw.strip().lower()
    try:
        marker = MarkerName(candidate)
    except ValueError:
        marker = None
    if marker is not None:
        return _marker_dt(day, marker, now) + timedelta(minutes=offset_min)
    try:
        hour_raw, minute_raw = candidate.split(":")
        slot = time(int(hour_raw), int(minute_raw))
    except ValueError:
        raise ConfigError(
            f"playlist window bound is not HH:MM or marker: {raw!r}"
        ) from None
    return datetime.combine(now.date(), slot, tzinfo=now.tzinfo)


def _playlist_window(
    playlist: Playlist, day: PrayerDay, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """Active window for one playlist; ``None`` bounds stay open."""
    if playlist.anchor_marker is not None:
        base = _marker_dt(day, playlist.anchor_marker, now)
        return (
            base + timedelta(minutes=playlist.anchor_start_offset_min),
            base + timedelta(minutes=playlist.anchor_stop_offset_min),
        )
    start = (
        _parse_bound(playlist.window_start, day, now, playlist.anchor_start_offset_min)
        if playlist.window_start is not None
        else None
    )
    stop = (
        _parse_bound(playlist.window_end, day, now, playlist.anchor_stop_offset_min)
        if playlist.window_end is not None
        else None
    )
    return start, stop


def _in_window(start: datetime | None, stop: datetime | None, now: datetime) -> bool:
    """Half-open ``[start, stop)`` membership; inverted bounds span midnight."""
    if start is not None and stop is not None and stop <= start:
        return now >= start or now < stop
    if start is not None and now < start:
        return False
    return stop is None or now < stop


def resolve_stage(
    now: datetime,
    day: PrayerDay,
    settings: Settings,
    event: NextEvent,
    playlists: Sequence[Playlist],
) -> StageOccupant:
    """Resolve the Main Stage occupier for one pinned instant.

    Countdown windows outrank everything; otherwise the most-recently
    activated in-window playlist wins; otherwise the Clock shows. A
    playlist counts only while active, non-empty, and in-window. Unknown
    window bounds raise ``ConfigError`` when the playlist is evaluated.
    """
    prayer = event.next_prayer
    if prayer is not None and event.adhan_at is not None:
        pre_window = timedelta(minutes=countdown_window(settings, prayer))
        if event.adhan_at - pre_window <= now < event.adhan_at:
            return CountdownOccupant(kind="adhan", prayer=prayer)
        adhan_end = event.adhan_at + timedelta(seconds=settings.adhan_duration_s)
        if event.iqamah_at is not None and adhan_end <= now < event.iqamah_at:
            return CountdownOccupant(kind="iqamah", prayer=prayer)
    best: tuple[datetime, PlaylistOccupant] | None = None
    for playlist in playlists:
        if not playlist.active or not playlist.items:
            continue
        start, stop = _playlist_window(playlist, day, now)
        if not _in_window(start, stop, now):
            continue
        activation = (
            start if start is not None else datetime.min.replace(tzinfo=now.tzinfo)
        )
        if best is None or activation > best[0]:
            best = (activation, PlaylistOccupant(playlist_id=playlist.id))
    return best[1] if best is not None else ClockOccupant()
