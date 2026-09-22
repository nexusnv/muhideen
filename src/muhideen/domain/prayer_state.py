"""Prayer state machine: pure resolution of display state over pinned time."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from muhideen.core.values import (
    IqamahRule,
    NextEvent,
    PrayerDay,
    PrayerName,
    PrayerState,
    Settings,
)
from muhideen.domain.iqamah import resolve_iqamah

PRE_ADHAN_WINDOW = timedelta(minutes=5)


def _adhan_dt(day: PrayerDay, slot: time, tz: ZoneInfo | None) -> datetime:
    return datetime.combine(day.date, slot, tzinfo=tz)


def resolve_next_event(
    now: datetime,
    today: PrayerDay,
    tomorrow: PrayerDay | None,
    rules: dict[PrayerName, IqamahRule],
    settings: Settings,
    stale: bool,
) -> NextEvent:
    """Compute the display state for one pinned instant."""
    adhan_duration = timedelta(seconds=settings.adhan_duration_s)
    tz = now.tzinfo
    assert tz is None or isinstance(tz, ZoneInfo)
    is_friday = today.date.weekday() == 4
    dhuhr_label = PrayerName.JUMUAH if is_friday else PrayerName.DHUHR
    slots: list[tuple[PrayerName, datetime, int]] = [
        (
            PrayerName.FAJR,
            _adhan_dt(today, today.fajr, tz),
            settings.dim_minutes_default,
        ),
        (PrayerName.SYURUQ, _adhan_dt(today, today.syuruq, tz), 0),
        (
            dhuhr_label,
            _adhan_dt(today, today.dhuhr, tz),
            settings.dim_minutes_jumuah if is_friday else settings.dim_minutes_default,
        ),
        (PrayerName.ASR, _adhan_dt(today, today.asr, tz), settings.dim_minutes_default),
        (
            PrayerName.MAGHRIB,
            _adhan_dt(today, today.maghrib, tz),
            settings.dim_minutes_default,
        ),
        (
            PrayerName.ISHA,
            _adhan_dt(today, today.isha, tz),
            settings.dim_minutes_default,
        ),
    ]
    for prayer, adhan_at, dim_minutes in slots:
        iqamah_at = resolve_iqamah(prayer, adhan_at, rules)
        if iqamah_at is None:
            adhan_end = adhan_at + adhan_duration
            if adhan_at <= now < adhan_end:
                return NextEvent(
                    now=now,
                    state=PrayerState.ADHAN,
                    next_prayer=prayer,
                    adhan_at=adhan_at,
                    iqamah_at=None,
                    dim_until=None,
                    stale=stale,
                )
            if adhan_at - PRE_ADHAN_WINDOW <= now < adhan_at:
                return NextEvent(
                    now=now,
                    state=PrayerState.PRE_ADHAN,
                    next_prayer=prayer,
                    adhan_at=adhan_at,
                    iqamah_at=None,
                    dim_until=None,
                    stale=stale,
                )
            continue
        dim_until = iqamah_at + timedelta(minutes=dim_minutes)
        adhan_end = adhan_at + adhan_duration
        if iqamah_at <= now < dim_until:
            return NextEvent(
                now=now,
                state=PrayerState.SALAH_DIM,
                next_prayer=prayer,
                adhan_at=adhan_at,
                iqamah_at=iqamah_at,
                dim_until=dim_until,
                stale=stale,
            )
        if adhan_end <= now < iqamah_at:
            return NextEvent(
                now=now,
                state=PrayerState.IQAMAH_COUNTDOWN,
                next_prayer=prayer,
                adhan_at=adhan_at,
                iqamah_at=iqamah_at,
                dim_until=dim_until,
                stale=stale,
            )
        if adhan_at <= now < adhan_end:
            return NextEvent(
                now=now,
                state=PrayerState.ADHAN,
                next_prayer=prayer,
                adhan_at=adhan_at,
                iqamah_at=iqamah_at,
                dim_until=dim_until,
                stale=stale,
            )
        if adhan_at - PRE_ADHAN_WINDOW <= now < adhan_at:
            return NextEvent(
                now=now,
                state=PrayerState.PRE_ADHAN,
                next_prayer=prayer,
                adhan_at=adhan_at,
                iqamah_at=iqamah_at,
                dim_until=dim_until,
                stale=stale,
            )
    for prayer, adhan_at, dim_minutes in slots:
        if adhan_at > now:
            iqamah_at = resolve_iqamah(prayer, adhan_at, rules)
            dim_until = (
                iqamah_at + timedelta(minutes=dim_minutes)
                if iqamah_at is not None
                else None
            )
            return NextEvent(
                now=now,
                state=PrayerState.NORMAL,
                next_prayer=prayer,
                adhan_at=adhan_at,
                iqamah_at=iqamah_at,
                dim_until=dim_until,
                stale=stale,
            )
    fajr_time = tomorrow.fajr if tomorrow is not None else today.fajr
    next_date = today.date + timedelta(days=1)
    fajr_dt = datetime.combine(next_date, fajr_time, tzinfo=tz)
    iqamah_at = resolve_iqamah(PrayerName.FAJR, fajr_dt, rules)
    assert iqamah_at is not None
    dim_until = iqamah_at + timedelta(minutes=settings.dim_minutes_default)
    if fajr_dt - PRE_ADHAN_WINDOW <= now < fajr_dt:
        return NextEvent(
            now=now,
            state=PrayerState.PRE_ADHAN,
            next_prayer=PrayerName.FAJR,
            adhan_at=fajr_dt,
            iqamah_at=iqamah_at,
            dim_until=dim_until,
            stale=stale,
        )
    return NextEvent(
        now=now,
        state=PrayerState.NORMAL,
        next_prayer=PrayerName.FAJR,
        adhan_at=fajr_dt,
        iqamah_at=iqamah_at,
        dim_until=dim_until,
        stale=stale,
    )
