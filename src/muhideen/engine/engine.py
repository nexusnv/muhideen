"""Engine orchestration: resolve day -> compute next event -> fan out.

The engine is the only place that composes ports into use-cases. It reads
no wall-clock time itself: `now` is an explicit parameter on `resolve_day`
and `next_event`, and `tick` takes it from the injected `Clock`. It imports
`core` + `domain` only — no HTTP, no SQL — so the layer contract
(`api -> engine -> adapters -> domain -> core`) holds.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from muhideen.core.errors import MuhideenError
from muhideen.core.ports import CalcEngine, Clock, EventBus, PrayerRepo, SettingsRepo
from muhideen.core.values import (
    MarkerName,
    NextEvent,
    PrayerDay,
    PrayerState,
    Settings,
)
from muhideen.domain import FallbackResult, resolve_next_event
from muhideen.domain import resolve_day as resolve_fallback

STATE_EVENT = "state"
TICK_EVENT = "tick"

# Fan-out key: everything a client's render depends on, deliberately
# excluding `event.now`, which changes on every call. The gated boundary
# pointer is included so a `boundary_countdown` opt-in flip fans out as
# `state` (FR-6.1 live-reload precedent), without ever changing state itself.
_Fingerprint = tuple[
    PrayerState,
    MarkerName | None,
    datetime | None,
    datetime | None,
    datetime | None,
    MarkerName | None,
    datetime | None,
    bool,
]
_Minute = tuple[int, int, int, int, int]


class Engine:
    """Resolve schedules and next events, publishing `state`/`tick` changes."""

    def __init__(
        self,
        *,
        settings_repo: SettingsRepo,
        prayer_repo: PrayerRepo,
        clock: Clock,
        event_bus: EventBus,
        calc: CalcEngine | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._prayer_repo = prayer_repo
        self._clock = clock
        self._event_bus = event_bus
        self._calc = calc
        self._last_fingerprint: _Fingerprint | None = None
        self._last_minute: _Minute | None = None

    def resolve_day(self, requested: date, zone: str, now: datetime) -> FallbackResult:
        """Resolve one day through the FR-1.2 chain: cache, calc, last-known."""
        settings = self._settings_repo.load()
        return self._resolve_day(requested, zone, now, settings)

    def next_event(self, now: datetime) -> NextEvent:
        """Compute the PRD §8 display state for one pinned instant.

        Carries the boundary pointer when `Settings.boundary_countdown` is
        on; toggling that opt-in fans out as `state` via the tick
        fingerprint (FR-6.1 live reload).
        """
        settings = self._settings_repo.load()
        zone = settings.zone  # one zone per installation
        today = self._resolve_day(now.date(), zone, now, settings)
        tomorrow = self._tomorrow(now.date() + timedelta(days=1), zone, settings)
        rules = {rule.prayer: rule for rule in settings.iqamah_rules}
        return resolve_next_event(
            now, today.day, tomorrow, rules, settings, today.stale
        )

    def tick(self) -> NextEvent:
        """Recompute on the clock and publish `state`/`tick` when they change."""
        now = self._clock.now()
        event = self.next_event(now)
        fingerprint: _Fingerprint = (
            event.state,
            event.next_prayer,
            event.adhan_at,
            event.iqamah_at,
            event.dim_until,
            event.next_boundary,
            event.boundary_at,
            event.stale,
        )
        minute: _Minute = (now.year, now.month, now.day, now.hour, now.minute)
        if fingerprint != self._last_fingerprint:
            self._event_bus.publish(STATE_EVENT)
            self._last_fingerprint = fingerprint
        if minute != self._last_minute:
            self._event_bus.publish(TICK_EVENT)
            self._last_minute = minute
        return event

    def _resolve_day(
        self, requested: date, zone: str, now: datetime, settings: Settings
    ) -> FallbackResult:
        # Lazy chain: calc and last-known are queried only when every
        # cheaper source already missed.
        cached = self._prayer_repo.get_day(requested, zone)
        calculated = (
            self._calc_day(requested, zone, settings) if cached is None else None
        )
        last_known = (
            self._prayer_repo.last_known(requested, zone)
            if cached is None and calculated is None
            else None
        )
        return resolve_fallback(requested, zone, now, cached, calculated, last_known)

    def _calc_day(self, day: date, zone: str, settings: Settings) -> PrayerDay | None:
        """Computed day stamped with the requested zone, or None if unavailable."""
        if self._calc is None or settings.lat is None or settings.lon is None:
            return None
        try:
            computed = self._calc.compute_day(
                day, settings.lat, settings.lon, settings.method
            )
        except (MuhideenError, ValueError):
            return None  # a broken calculator is a cache miss, not a 500
        if computed.zone != zone:
            # CalcEngine.compute_day has no zone parameter, but the fallback
            # chain requires a zone match.
            computed = replace(computed, zone=zone)
        return computed

    def _tomorrow(self, day: date, zone: str, settings: Settings) -> PrayerDay | None:
        # `last_known` is deliberately never used for tomorrow: a past
        # template day must not seed tomorrow's Fajr.
        cached = self._prayer_repo.get_day(day, zone)
        if cached is not None:
            return cached
        return self._calc_day(day, zone, settings)
