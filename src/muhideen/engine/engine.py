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

from muhideen.core.errors import MuhideenError, ScheduleError
from muhideen.core.ports import (
    CalcEngine,
    Clock,
    EventBus,
    PrayerRepo,
    SettingsRepo,
    TimeSyncProbe,
)
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

# TIME UNSYNCED (FR-1.6): a wall-clock step larger than `_DRIFT_STEP_S`
# that monotonic time did not observe latches the display unsynced for
# `_DRIFT_LATCH_S` injected-monotonic seconds — long enough that an NTP
# step or manual wall bump cannot masquerade as healthy on the next poll.
_DRIFT_STEP_S = 5.0
_DRIFT_LATCH_S = 300.0

# Fan-out key: everything a client's render depends on, deliberately
# excluding `event.now`, which changes on every call. The gated boundary
# pointer is included so a `boundary_countdown` opt-in flip fans out as
# `state` (FR-6.1 live-reload precedent), without ever changing state itself.
# `time_synced` rides the same way: an NTP health flip fans out as `state`.
_Fingerprint = tuple[
    PrayerState,
    MarkerName | None,
    datetime | None,
    datetime | None,
    datetime | None,
    MarkerName | None,
    datetime | None,
    bool,
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
        time_sync: TimeSyncProbe | None = None,
    ) -> None:
        """Hold the injected repos, clock, bus, and optional calc/sync."""
        self._settings_repo = settings_repo
        self._prayer_repo = prayer_repo
        self._clock = clock
        self._event_bus = event_bus
        self._calc = calc
        self._time_sync = time_sync
        self._drift_sample: tuple[datetime, float] | None = None
        self._drift_latch_until: float | None = None
        self._last_fingerprint: _Fingerprint | None = None
        self._last_minute: _Minute | None = None

    def resolve_day(self, requested: date, zone: str, now: datetime) -> FallbackResult:
        """Resolve one day through the FR-1.2 chain: cache, calc, last-known.

        Only the configured zone resolves; anything else is an unknown
        schedule (404 at the API), never a calc result stamped as requested.
        """
        settings = self._settings_repo.load()
        if zone != settings.zone:
            raise ScheduleError(f"unknown zone: {zone}", zone, requested.isoformat())
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
        event = resolve_next_event(
            now, today.day, tomorrow, rules, settings, today.stale
        )
        return replace(event, time_synced=self._time_synced())

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
            event.time_synced,
        )
        minute: _Minute = (now.year, now.month, now.day, now.hour, now.minute)
        if fingerprint != self._last_fingerprint:
            self._event_bus.publish(STATE_EVENT)
            self._last_fingerprint = fingerprint
        if minute != self._last_minute:
            self._event_bus.publish(TICK_EVENT)
            self._last_minute = minute
        return event

    def _time_synced(self) -> bool:
        """NTP health for one `next_event` stamp: probe answer + drift latch.

        No probe means synced (dev/contract defaults, FR-1.6 contract field
        stays `true`). With a probe, a wall step the injected monotonic clock
        did not observe (`|(Δwall − Δmono)| > 5.0s`) latches unsynced for
        `_DRIFT_LATCH_S`; the latch clears only once it has aged out *and*
        the probe still reports synced, so a single manual time bump cannot
        flip the banner back on the next poll.
        """
        if self._time_sync is None:
            return True
        synced = self._time_sync.synchronized()
        wall = self._clock.now()
        mono = self._clock.monotonic()
        if self._drift_sample is not None:
            prev_wall, prev_mono = self._drift_sample
            step = abs((wall - prev_wall).total_seconds() - (mono - prev_mono))
            if step > _DRIFT_STEP_S:
                self._drift_latch_until = mono + _DRIFT_LATCH_S
        self._drift_sample = (wall, mono)
        if (
            self._drift_latch_until is not None
            and mono >= self._drift_latch_until
            and synced
        ):
            self._drift_latch_until = None
        return synced and self._drift_latch_until is None

    def _resolve_day(
        self, requested: date, zone: str, now: datetime, settings: Settings
    ) -> FallbackResult:
        """Walk cache, then calc, then last-known; miss only when all miss."""
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
        """Fetch tomorrow from cache or calc; never from last-known."""
        # `last_known` is deliberately never used for tomorrow: a past
        # template day must not seed tomorrow's Fajr.
        cached = self._prayer_repo.get_day(day, zone)
        if cached is not None:
            return cached
        return self._calc_day(day, zone, settings)
