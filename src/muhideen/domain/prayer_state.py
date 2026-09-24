"""Prayer state machine: pure resolution of display state over pinned time.

State windows are driven exclusively by the 5 Prayer Time Markers (fajr,
dhuhr/jumuah, asr, maghrib, isha). Boundary Time Markers (imsak, syuruq,
dhuha) never enter a state — they only ever contribute the opt-in
``next_boundary``/``boundary_at`` pointer (PRD FR-1.7).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from muhideen.core.values import (
    IqamahRule,
    MarkerName,
    NextEvent,
    PrayerDay,
    PrayerState,
    Settings,
)
from muhideen.domain.iqamah import resolve_iqamah

PRE_ADHAN_WINDOW = timedelta(minutes=5)


def _adhan_dt(day_date: date, slot: time, tz: tzinfo | None) -> datetime:
    """Combine a schedule clock time with its calendar date and zone."""
    return datetime.combine(day_date, slot, tzinfo=tz)


def _next_boundary(
    effective: date,
    today: PrayerDay,
    tomorrow: PrayerDay | None,
    tz: tzinfo | None,
    now: datetime,
) -> tuple[MarkerName, datetime]:
    """Next upcoming Boundary Time Marker instant, carrying to tomorrow's
    first marker (imsak) once all of today's have passed — mirrors the
    fajr template carry in ``resolve_next_event``."""
    for marker, slot in (
        (MarkerName.IMSAK, today.imsak),
        (MarkerName.SYURUQ, today.syuruq),
        (MarkerName.DHUHA, today.dhuha),
    ):
        boundary_at = _adhan_dt(effective, slot, tz)
        if boundary_at > now:
            return marker, boundary_at
    imsak_time = tomorrow.imsak if tomorrow is not None else today.imsak
    next_date = effective + timedelta(days=1)
    return MarkerName.IMSAK, _adhan_dt(next_date, imsak_time, tz)


def resolve_next_event(
    now: datetime,
    today: PrayerDay,
    tomorrow: PrayerDay | None,
    rules: dict[MarkerName, IqamahRule],
    settings: Settings,
    stale: bool,
) -> NextEvent:
    """Compute the display state for one pinned instant.

    Schedule clock times are read from ``today`` but anchored on
    ``now.date()``: a last-known day from another date (the FR-1.2 offline
    path, which ``resolve_day`` explicitly allows) renders as a template for
    today instead of emitting a past ``adhan_at``. Friday detection likewise
    follows ``now.date()`` so Jumuah still replaces Dhuhr on Fridays.
    Any tz-aware ``now`` is accepted, including the fixed-offset ``+08:00``
    form used throughout the API contract.

    The boundary pointer is gated by ``settings.boundary_countdown`` and is
    never an input to the state decision.
    """
    adhan_duration = timedelta(seconds=settings.adhan_duration_s)
    tz = now.tzinfo
    effective = now.date()
    is_friday = effective.weekday() == 4
    dhuhr_label = MarkerName.JUMUAH if is_friday else MarkerName.DHUHR
    slots: list[tuple[MarkerName, datetime, int]] = [
        (
            MarkerName.FAJR,
            _adhan_dt(effective, today.fajr, tz),
            settings.dim_minutes_default,
        ),
        (
            dhuhr_label,
            _adhan_dt(effective, today.dhuhr, tz),
            settings.dim_minutes_jumuah if is_friday else settings.dim_minutes_default,
        ),
        (
            MarkerName.ASR,
            _adhan_dt(effective, today.asr, tz),
            settings.dim_minutes_default,
        ),
        (
            MarkerName.MAGHRIB,
            _adhan_dt(effective, today.maghrib, tz),
            settings.dim_minutes_default,
        ),
        (
            MarkerName.ISHA,
            _adhan_dt(effective, today.isha, tz),
            settings.dim_minutes_default,
        ),
    ]
    next_boundary, boundary_at = (
        _next_boundary(effective, today, tomorrow, tz, now)
        if settings.boundary_countdown
        else (None, None)
    )

    def _event(
        state: PrayerState,
        prayer: MarkerName,
        adhan_at: datetime,
        iqamah_at: datetime | None,
        dim_until: datetime | None,
    ) -> NextEvent:
        """Assemble one NextEvent with the shared boundary pointer attached."""
        return NextEvent(
            now=now,
            state=state,
            next_prayer=prayer,
            adhan_at=adhan_at,
            iqamah_at=iqamah_at,
            dim_until=dim_until,
            stale=stale,
            next_boundary=next_boundary,
            boundary_at=boundary_at,
        )

    for prayer, adhan_at, dim_minutes in slots:
        iqamah_at = resolve_iqamah(prayer, adhan_at, rules)
        dim_until = iqamah_at + timedelta(minutes=dim_minutes)
        adhan_end = adhan_at + adhan_duration
        # ADHAN is checked first: when adhan_duration_s outlasts the gap to
        # iqamah (short/fixed rule), the overlay must win over the states it
        # overlaps, per PRD §8 ordering.
        if adhan_at <= now < adhan_end:
            return _event(PrayerState.ADHAN, prayer, adhan_at, iqamah_at, dim_until)
        if iqamah_at <= now < dim_until:
            return _event(PrayerState.SALAH_DIM, prayer, adhan_at, iqamah_at, dim_until)
        if adhan_end <= now < iqamah_at:
            return _event(
                PrayerState.IQAMAH_COUNTDOWN, prayer, adhan_at, iqamah_at, dim_until
            )
        if adhan_at - PRE_ADHAN_WINDOW <= now < adhan_at:
            return _event(PrayerState.PRE_ADHAN, prayer, adhan_at, iqamah_at, dim_until)
    for prayer, adhan_at, dim_minutes in slots:
        if adhan_at > now:
            iqamah_at = resolve_iqamah(prayer, adhan_at, rules)
            dim_until = iqamah_at + timedelta(minutes=dim_minutes)
            return _event(PrayerState.NORMAL, prayer, adhan_at, iqamah_at, dim_until)
    fajr_time = tomorrow.fajr if tomorrow is not None else today.fajr
    next_date = effective + timedelta(days=1)
    fajr_dt = _adhan_dt(next_date, fajr_time, tz)
    iqamah_at = resolve_iqamah(MarkerName.FAJR, fajr_dt, rules)
    dim_until = iqamah_at + timedelta(minutes=settings.dim_minutes_default)
    return _event(PrayerState.NORMAL, MarkerName.FAJR, fajr_dt, iqamah_at, dim_until)
