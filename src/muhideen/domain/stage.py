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
from datetime import datetime, timedelta
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
from muhideen.domain.playlist_window import (
    is_in_window as _shared_in_window,
)
from muhideen.domain.playlist_window import (
    parse_window,
    window_datetimes,
)


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


def _playlist_window(
    playlist: Playlist, day: PrayerDay, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """Active window for one playlist; ``None`` bounds stay open."""
    try:
        typed = parse_window(playlist)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    return window_datetimes(typed, day, now)


def _in_window(start: datetime | None, stop: datetime | None, now: datetime) -> bool:
    """Half-open ``[start, stop)`` membership; inverted bounds span midnight."""
    return _shared_in_window(start, stop, now)


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
    playlist counts only while active, non-empty, in-window, and inside
    its cycle budget (``repeat`` releases after ``max_cycles`` full loops
    of the item set from the window start; ``indefinite`` loops forever).
    Unknown window bounds raise ``ConfigError`` when the playlist is
    evaluated.
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
        if start is not None and stop is not None and stop <= start and now < stop:
            activation = start - timedelta(days=1)
        if (
            playlist.cycle_mode == "repeat"
            and playlist.max_cycles is not None
            and start is not None
        ):
            cycle_total = sum(item.duration_s for item in playlist.items)
            if cycle_total <= 0:
                continue
            elapsed = (now - activation).total_seconds()
            if int(elapsed // cycle_total) >= playlist.max_cycles:
                continue
        if best is None or activation > best[0]:
            best = (activation, PlaylistOccupant(playlist_id=playlist.id))
    return best[1] if best is not None else ClockOccupant()
