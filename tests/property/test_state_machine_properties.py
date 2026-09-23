"""Property invariants of the PRD §8 prayer state machine (slice 1A-4).

These pin already-landed pure-domain behaviour under generated schedules:
monotonic targets, disjoint state windows driven only by Prayer Time
Markers, the Boundary Time Marker edge, the midnight crossover, and
re-render idempotence. A failure here is a domain defect or a wrong
strategy bound — never weaken an invariant to get green.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import TypeAlias
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    IqamahRule,
    MarkerKind,
    MarkerName,
    PrayerDay,
    PrayerState,
    ScheduleSource,
    Settings,
    marker_kind,
)
from muhideen.domain import resolve_next_event

pytestmark = pytest.mark.property

TZ = ZoneInfo("Asia/Kuala_Lumpur")
# Wednesday + Friday, so the Jumuah relabel runs under property pressure.
DATES = st.sampled_from([date(2025, 10, 22), date(2025, 10, 24)])
PRE_ADHAN_MINUTES = 5  # PRD §8: hide 5 minutes before the adhan
# Test-space bound: the widest possible default window span is
# 5 (PRE_ADHAN) + 5 (adhan_duration_s <= 300) + 15 (Fajr/Isha delay) +
# 45 (Jumuah dim) = 70 minutes, so a gap of 80 minutes keeps the windows of
# adjacent slots disjoint. This is a bound on the generated test space, not a
# product claim about minimum prayer spacing.
MIN_GAP = 80

Case: TypeAlias = tuple[PrayerDay, Settings, datetime]


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=TZ)


def _rules(settings: Settings) -> dict[MarkerName, IqamahRule]:
    return {rule.prayer: rule for rule in settings.iqamah_rules}


@st.composite
def _minutes(draw: st.DrawFn, max_minute: int = 1439) -> list[int]:
    """Eight strictly increasing minute-of-day slots with gaps >= MIN_GAP."""
    first = draw(st.integers(0, max_minute - 7 * MIN_GAP))
    minutes = [first]
    previous = first
    for gaps_left_after in range(6, -1, -1):
        upper = max_minute - previous - MIN_GAP * gaps_left_after
        previous += draw(st.integers(MIN_GAP, upper))
        minutes.append(previous)
    return minutes


@st.composite
def _scenario(
    draw: st.DrawFn,
    max_minute: int = 1439,
    now_start: int = 0,
    now_end: int = 86399,
) -> Case:
    day_date = draw(DATES)
    slots = draw(_minutes(max_minute))
    duration_s = draw(st.integers(60, 300))
    now = _at(day_date, time(0, 0)) + timedelta(
        seconds=draw(st.integers(now_start, now_end))
    )
    imsak, fajr, syuruq, dhuha, dhuhr, asr, maghrib, isha = (
        time(minute // 60, minute % 60) for minute in slots
    )
    day = PrayerDay(
        date=day_date,
        zone="SGR01",
        imsak=imsak,
        fajr=fajr,
        syuruq=syuruq,
        dhuha=dhuha,
        dhuhr=dhuhr,
        asr=asr,
        maghrib=maghrib,
        isha=isha,
        source=ScheduleSource.JAKIM,
        fetched_at=now - timedelta(hours=1),
    )
    settings = Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        adhan_duration_s=duration_s,
        iqamah_rules=DEFAULT_IQAMAH_RULES,
        # The flag only gates the pointer: every property below runs under
        # both values and must hold either way.
        boundary_countdown=draw(st.booleans()),
    )
    return day, settings, now


def _windows(
    day: PrayerDay, settings: Settings
) -> list[tuple[datetime, datetime, PrayerState]]:
    """Independent PRD §8 oracle: the state windows of one schedule day."""
    rules = _rules(settings)
    duration = timedelta(seconds=settings.adhan_duration_s)
    pre_adhan = timedelta(minutes=PRE_ADHAN_MINUTES)
    is_friday = day.date.weekday() == 4
    windows: list[tuple[datetime, datetime, PrayerState]] = []
    for prayer, moment in (
        (MarkerName.FAJR, day.fajr),
        (MarkerName.DHUHR, day.dhuhr),
        (MarkerName.ASR, day.asr),
        (MarkerName.MAGHRIB, day.maghrib),
        (MarkerName.ISHA, day.isha),
    ):
        adhan_at = _at(day.date, moment)
        windows.append((adhan_at - pre_adhan, adhan_at, PrayerState.PRE_ADHAN))
        windows.append((adhan_at, adhan_at + duration, PrayerState.ADHAN))
        label = (
            MarkerName.JUMUAH if is_friday and prayer is MarkerName.DHUHR else prayer
        )
        rule = rules[label]
        assert rule.mode == "delay" and rule.fixed_time is None
        iqamah_at = adhan_at + timedelta(minutes=rule.delay_minutes)
        dim_minutes = (
            settings.dim_minutes_jumuah
            if label is MarkerName.JUMUAH
            else settings.dim_minutes_default
        )
        windows.append((adhan_at + duration, iqamah_at, PrayerState.IQAMAH_COUNTDOWN))
        windows.append(
            (
                iqamah_at,
                iqamah_at + timedelta(minutes=dim_minutes),
                PrayerState.SALAH_DIM,
            )
        )
    return windows


@given(_scenario())
def test_targets_are_monotonic(case: Case) -> None:
    day, settings, now = case
    event = resolve_next_event(now, day, None, _rules(settings), settings, False)
    assert event.now == now
    if event.iqamah_at is not None:
        assert event.adhan_at is not None
        assert event.adhan_at <= event.iqamah_at
    if event.dim_until is not None:
        assert event.iqamah_at is not None
        assert event.iqamah_at <= event.dim_until
    assert event.adhan_at is not None
    duration = timedelta(seconds=settings.adhan_duration_s)
    pre_adhan = timedelta(minutes=PRE_ADHAN_MINUTES)
    if event.state is PrayerState.ADHAN:
        assert event.adhan_at <= now
    elif event.state is PrayerState.IQAMAH_COUNTDOWN:
        assert event.iqamah_at is not None
        assert event.adhan_at + duration <= now < event.iqamah_at
    elif event.state is PrayerState.SALAH_DIM:
        assert event.iqamah_at is not None and event.iqamah_at <= now
        assert event.dim_until is not None and now < event.dim_until
    elif event.state is PrayerState.PRE_ADHAN:
        assert event.adhan_at - pre_adhan <= now < event.adhan_at
    else:
        assert event.state is PrayerState.NORMAL
        assert now < event.adhan_at


@given(_scenario())
def test_state_windows_do_not_overlap(case: Case) -> None:
    day, settings, _ = case
    windows = _windows(day, settings)
    for minute in range(0, 24 * 60, 5):
        now = datetime.combine(day.date, time(minute // 60, minute % 60), tzinfo=TZ)
        containing = [state for start, end, state in windows if start <= now < end]
        assert len(containing) <= 1, f"overlapping windows at {now}: {containing}"
        event = resolve_next_event(now, day, None, _rules(settings), settings, False)
        if containing:
            assert event.state is containing[0]
        else:
            assert event.state is PrayerState.NORMAL


@given(_scenario())
def test_next_prayer_is_always_a_prayer_marker(case: Case) -> None:
    day, settings, now = case
    event = resolve_next_event(now, day, None, _rules(settings), settings, False)
    assert event.next_prayer in {
        MarkerName.FAJR,
        MarkerName.DHUHR,
        MarkerName.JUMUAH,
        MarkerName.ASR,
        MarkerName.MAGHRIB,
        MarkerName.ISHA,
    }


@given(_scenario(max_minute=1320, now_start=23 * 3600, now_end=86399))
def test_midnight_resolves_next_day_fajr(case: Case) -> None:
    """23:00-24:00 with Isha <= 22:00 is always past every window today."""
    day, settings, now = case
    event = resolve_next_event(now, day, None, _rules(settings), settings, False)
    assert event.state is PrayerState.NORMAL
    assert event.next_prayer is MarkerName.FAJR
    assert event.adhan_at is not None
    expected = datetime.combine(now.date() + timedelta(days=1), day.fajr, tzinfo=TZ)
    assert event.adhan_at == expected
    assert event.adhan_at.time() == day.fajr
    assert event.iqamah_at == event.adhan_at + timedelta(minutes=15)


@given(_scenario())
def test_resolution_is_idempotent(case: Case) -> None:
    day, settings, now = case
    first = resolve_next_event(now, day, None, _rules(settings), settings, False)
    second = resolve_next_event(now, day, None, _rules(settings), settings, False)
    assert first == second
    assert hash(first) == hash(second)


@given(_scenario())
def test_boundary_pointer_present_iff_opted_in(case: Case) -> None:
    day, settings, now = case
    event = resolve_next_event(now, day, None, _rules(settings), settings, False)
    if settings.boundary_countdown:
        assert event.next_boundary is not None
        assert event.boundary_at is not None
    else:
        assert event.next_boundary is None
        assert event.boundary_at is None


@given(_scenario())
def test_boundary_pointer_is_future_and_tz_aware(case: Case) -> None:
    day, settings, now = case
    event = resolve_next_event(now, day, None, _rules(settings), settings, False)
    if event.next_boundary is None:
        return
    assert marker_kind(event.next_boundary) is MarkerKind.BOUNDARY
    assert event.boundary_at is not None
    assert event.boundary_at.tzinfo is not None
    # Today's upcoming marker or tomorrow's carried imsak — never a past instant.
    assert event.boundary_at > event.now


@given(_scenario())
def test_boundary_pointer_never_overlaps_prayer_targets(case: Case) -> None:
    """The opt-in changes only the pointer: state/prayer targets identical."""
    day, settings, now = case
    rules = _rules(settings)
    off = resolve_next_event(
        now, day, None, rules, replace(settings, boundary_countdown=False), False
    )
    on = resolve_next_event(
        now, day, None, rules, replace(settings, boundary_countdown=True), False
    )
    assert off.state is on.state
    assert off.next_prayer is on.next_prayer
    assert off.adhan_at == on.adhan_at
    assert off.iqamah_at == on.iqamah_at
    assert off.dim_until == on.dim_until
    assert off.next_boundary is None
    assert on.next_boundary is not None
    assert on.boundary_at is not None
