"""Main Stage occupancy matrix (slice 1C, Task 5)."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.values import (
    CycleMode,
    IqamahRule,
    MarkerName,
    NextEvent,
    Playlist,
    PlaylistItem,
    PrayerDay,
    PrayerState,
    ScheduleSource,
    Settings,
)

TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 22),
        zone="SGR01",
        imsak=dtime(5, 35),
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuha=dtime(7, 25),
        dhuhr=dtime(12, 15),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 22, 1, 0, tzinfo=TZ),
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _at(hour: int, minute: int) -> datetime:
    return datetime(2025, 10, 22, hour, minute, tzinfo=TZ)


def _event(
    now: datetime,
    prayer: MarkerName,
    adhan_at: datetime,
    iqamah_at: datetime,
) -> NextEvent:
    return NextEvent(
        now=now,
        state=PrayerState.NORMAL,
        next_prayer=prayer,
        adhan_at=adhan_at,
        iqamah_at=iqamah_at,
        dim_until=iqamah_at,
        stale=False,
    )


def _dhuhr_event(now: datetime) -> NextEvent:
    return _event(
        now,
        MarkerName.DHUHR,
        datetime(2025, 10, 22, 12, 15, tzinfo=TZ),
        datetime(2025, 10, 22, 12, 25, tzinfo=TZ),
    )


def _item(path: str = "img/a.jpg") -> PlaylistItem:
    return PlaylistItem(image_path=path, duration_s=10, sort_order=0)


def _playlist(
    pid: str,
    start: str | None = None,
    end: str | None = None,
    *,
    active: bool = True,
    items: tuple[PlaylistItem, ...] = (_item(),),
    anchor: MarkerName | None = None,
    start_offset: int = 0,
    stop_offset: int = 0,
    cycle_mode: CycleMode = "indefinite",
    max_cycles: int | None = None,
) -> Playlist:
    return Playlist(
        id=pid,
        title=pid,
        active=active,
        window_start=start,
        window_end=end,
        anchor_marker=anchor,
        anchor_start_offset_min=start_offset,
        anchor_stop_offset_min=stop_offset,
        cycle_mode=cycle_mode,
        max_cycles=max_cycles,
        items=items,
    )


@pytest.mark.unit
def test_empty_playlists_resolves_clock() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), ()) == (
        ClockOccupant()
    )


@pytest.mark.unit
def test_single_in_window_playlist_occupies() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = _at(10, 0)
    occupant = resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (_playlist("a", "09:00", "11:00"),)
    )
    assert occupant == PlaylistOccupant(playlist_id="a")


@pytest.mark.unit
def test_inactive_playlist_ignored() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    occupant = resolve_stage(
        now,
        _day(),
        _settings(),
        _dhuhr_event(now),
        (_playlist("a", "09:00", "11:00", active=False),),
    )
    assert occupant == ClockOccupant()


@pytest.mark.unit
def test_playlist_without_items_ignored() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    alone = _playlist("a", "09:00", "11:00", items=())
    occupant = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (alone,))
    assert occupant == ClockOccupant()


@pytest.mark.unit
def test_out_of_window_playlist_ignored() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    occupant = resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (_playlist("a", "14:00", "15:00"),)
    )
    assert occupant == ClockOccupant()


@pytest.mark.unit
def test_overlap_most_recent_activation_wins() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = _at(10, 30)
    early = _playlist("early", "09:00", "12:00")
    late = _playlist("late", "10:00", "13:00")
    assert resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (early, late)
    ) == PlaylistOccupant(playlist_id="late")
    assert resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (late, early)
    ) == PlaylistOccupant(playlist_id="late")


@pytest.mark.unit
@pytest.mark.parametrize("order", [["night", "dawn"], ["dawn", "night"]])
def test_midnight_overlap_most_recent_activation_wins(order: list[str]) -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = _at(1, 0)
    windows = {
        "night": _playlist("night", "22:00", "02:00"),
        "dawn": _playlist("dawn", "00:30", "01:30"),
    }
    playlists = tuple(windows[pid] for pid in order)
    assert resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), playlists
    ) == PlaylistOccupant(playlist_id="dawn")


@pytest.mark.unit
def test_open_window_tie_keeps_input_order() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = _at(10, 0)
    first = _playlist("first")
    second = _playlist("second")
    assert resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (first, second)
    ) == PlaylistOccupant(playlist_id="first")
    assert resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (second, first)
    ) == PlaylistOccupant(playlist_id="second")


@pytest.mark.unit
def test_pre_adhan_countdown_overrides_playlist() -> None:
    from muhideen.domain.stage import CountdownOccupant, PlaylistOccupant, resolve_stage

    playlists = (_playlist("a", "12:00", "13:00"),)
    inside = resolve_stage(
        now := datetime(2025, 10, 22, 12, 11, tzinfo=TZ),
        _day(),
        _settings(),
        _dhuhr_event(now),
        playlists,
    )
    assert inside == CountdownOccupant(kind="adhan", prayer=MarkerName.DHUHR)
    outside = resolve_stage(
        now := datetime(2025, 10, 22, 12, 9, tzinfo=TZ),
        _day(),
        _settings(),
        _dhuhr_event(now),
        playlists,
    )
    assert outside == PlaylistOccupant(playlist_id="a")


@pytest.mark.unit
def test_pre_adhan_per_prayer_override() -> None:
    from muhideen.domain.stage import CountdownOccupant, PlaylistOccupant, resolve_stage

    now = datetime(2025, 10, 22, 5, 37, tzinfo=TZ)
    event = _event(
        now,
        MarkerName.FAJR,
        datetime(2025, 10, 22, 5, 45, tzinfo=TZ),
        datetime(2025, 10, 22, 6, 0, tzinfo=TZ),
    )
    playlists = (_playlist("a", "05:00", "06:00"),)
    widened = _settings(countdown_before_adhan_overrides={"fajr": 10})
    assert resolve_stage(now, _day(), widened, event, playlists) == CountdownOccupant(
        kind="adhan", prayer=MarkerName.FAJR
    )
    assert resolve_stage(now, _day(), _settings(), event, playlists) == (
        PlaylistOccupant(playlist_id="a")
    )


@pytest.mark.unit
def test_iqamah_window_overrides_playlist() -> None:
    from muhideen.domain.stage import CountdownOccupant, resolve_stage

    now = datetime(2025, 10, 22, 12, 20, tzinfo=TZ)
    occupant = resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (_playlist("a", "12:00", "13:00"),)
    )
    assert occupant == CountdownOccupant(kind="iqamah", prayer=MarkerName.DHUHR)


@pytest.mark.unit
def test_adhan_overlay_falls_through_to_playlist() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = datetime(2025, 10, 22, 12, 16, tzinfo=TZ)
    occupant = resolve_stage(
        now, _day(), _settings(), _dhuhr_event(now), (_playlist("a", "12:00", "13:00"),)
    )
    assert occupant == PlaylistOccupant(playlist_id="a")


@pytest.mark.unit
def test_marker_anchored_window_math() -> None:
    from muhideen.domain.stage import ClockOccupant, PlaylistOccupant, resolve_stage

    anchored = _playlist("anchored", "asr", "maghrib", start_offset=25, stop_offset=-60)
    # Asr 15:30 + 25 = 15:55; Maghrib 18:05 - 60 = 17:05.
    for hour, minute, expected in [
        (15, 50, ClockOccupant()),
        (15, 55, PlaylistOccupant(playlist_id="anchored")),
        (16, 30, PlaylistOccupant(playlist_id="anchored")),
        (17, 5, ClockOccupant()),
    ]:
        now = _at(hour, minute)
        got = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (anchored,))
        assert got == expected


@pytest.mark.unit
def test_single_anchor_marker_window() -> None:
    from muhideen.domain.stage import ClockOccupant, PlaylistOccupant, resolve_stage

    anchored = _playlist(
        "anchored", anchor=MarkerName.MAGHRIB, start_offset=-60, stop_offset=30
    )
    # Maghrib 18:05 - 60 = 17:05; Maghrib 18:05 + 30 = 18:35.
    now = _at(17, 30)
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (anchored,)) == (
        PlaylistOccupant(playlist_id="anchored")
    )
    now = _at(19, 0)
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (anchored,)) == (
        ClockOccupant()
    )


@pytest.mark.unit
def test_overnight_window() -> None:
    from muhideen.domain.stage import ClockOccupant, PlaylistOccupant, resolve_stage

    night = _playlist("night", "22:00", "02:00")
    for hour, minute, expected in [
        (23, 0, PlaylistOccupant(playlist_id="night")),
        (1, 0, PlaylistOccupant(playlist_id="night")),
        (3, 0, ClockOccupant()),
        (21, 0, ClockOccupant()),
    ]:
        now = _at(hour, minute)
        got = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (night,))
        assert got == expected


@pytest.mark.unit
def test_open_window_always_active() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    always = _playlist("always")
    for hour, minute in [(0, 5), (10, 0), (23, 59)]:
        now = _at(hour, minute)
        got = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (always,))
        assert got == PlaylistOccupant(playlist_id="always")


@pytest.mark.unit
def test_max_cycles_none_loops_indefinitely() -> None:
    from muhideen.domain.stage import PlaylistOccupant, resolve_stage

    now = datetime(2025, 10, 22, 10, 59, tzinfo=TZ)
    alone = _playlist("a", "09:00", "11:00", max_cycles=None)
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (alone,)) == (
        PlaylistOccupant(playlist_id="a")
    )


@pytest.mark.unit
def test_max_cycles_one_exhausts_after_one_full_cycle() -> None:
    from muhideen.domain.stage import ClockOccupant, PlaylistOccupant, resolve_stage

    capped = _playlist("a", "09:00", "11:00", cycle_mode="repeat", max_cycles=1)
    fallback = _playlist("b")
    before = datetime(2025, 10, 22, 9, 0, 5, tzinfo=TZ)
    assert resolve_stage(
        before, _day(), _settings(), _dhuhr_event(before), (capped,)
    ) == PlaylistOccupant(playlist_id="a")
    past = datetime(2025, 10, 22, 9, 0, 10, tzinfo=TZ)
    assert (
        resolve_stage(past, _day(), _settings(), _dhuhr_event(past), (capped,))
        == ClockOccupant()
    )
    assert resolve_stage(
        past, _day(), _settings(), _dhuhr_event(past), (capped, fallback)
    ) == PlaylistOccupant(playlist_id="b")


@pytest.mark.unit
def test_max_cycles_two_boundary() -> None:
    from muhideen.domain.stage import ClockOccupant, PlaylistOccupant, resolve_stage

    capped = _playlist("a", "09:00", "11:00", cycle_mode="repeat", max_cycles=2)
    last_second = datetime(2025, 10, 22, 9, 0, 19, tzinfo=TZ)
    assert resolve_stage(
        last_second, _day(), _settings(), _dhuhr_event(last_second), (capped,)
    ) == PlaylistOccupant(playlist_id="a")
    exhausted = datetime(2025, 10, 22, 9, 0, 20, tzinfo=TZ)
    assert (
        resolve_stage(
            exhausted, _day(), _settings(), _dhuhr_event(exhausted), (capped,)
        )
        == ClockOccupant()
    )


@pytest.mark.unit
def test_max_cycles_empty_items_stays_clock() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    alone = _playlist(
        "a", "09:00", "11:00", items=(), cycle_mode="repeat", max_cycles=1
    )
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (alone,)) == (
        ClockOccupant()
    )


@pytest.mark.unit
def test_invalid_window_bound_raises() -> None:
    from muhideen.core.errors import ConfigError
    from muhideen.core.values import Playlist as PlaylistVO
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    now = _at(10, 0)
    broken = PlaylistVO(
        id="broken",
        title="broken",
        active=True,
        window_start="bogus",
        window_end=None,
        anchor_marker=None,
        anchor_start_offset_min=0,
        anchor_stop_offset_min=0,
        cycle_mode="indefinite",
        max_cycles=None,
        items=(_item(),),
    )
    with pytest.raises(ConfigError):
        resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (broken,))
    ignored = _playlist("ignored", "bogus", None, active=False)
    assert resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (ignored,)) == (
        ClockOccupant()
    )


@pytest.mark.unit
def test_stage_shares_parser_with_write_seam() -> None:
    import muhideen.domain.stage as stage

    assert not hasattr(stage, "_parse_bound")
    assert hasattr(stage, "_playlist_window")
    assert hasattr(stage, "_in_window")


@pytest.mark.unit
def test_stage_id_strings() -> None:
    from muhideen.domain.stage import (
        ClockOccupant,
        CountdownOccupant,
        PlaylistOccupant,
        stage_id,
    )

    assert stage_id(ClockOccupant()) == "clock"
    assert (
        stage_id(CountdownOccupant(kind="adhan", prayer=MarkerName.FAJR))
        == "countdown:adhan:fajr"
    )
    assert (
        stage_id(CountdownOccupant(kind="iqamah", prayer=MarkerName.DHUHR))
        == "countdown:iqamah:dhuhr"
    )
    assert stage_id(PlaylistOccupant(playlist_id="a")) == "playlist:a"


@pytest.mark.unit
def test_resolve_next_event_feeds_stage() -> None:
    from muhideen.domain.prayer_state import resolve_next_event
    from muhideen.domain.stage import CountdownOccupant, resolve_stage

    rules = {
        prayer: IqamahRule(prayer=prayer, mode="delay", delay_minutes=minutes)
        for prayer, minutes in [
            (MarkerName.FAJR, 15),
            (MarkerName.DHUHR, 10),
            (MarkerName.ASR, 10),
            (MarkerName.MAGHRIB, 10),
            (MarkerName.ISHA, 15),
            (MarkerName.JUMUAH, 10),
        ]
    }
    settings = _settings()
    pre = datetime(2025, 10, 22, 12, 11, tzinfo=TZ)
    event = resolve_next_event(pre, _day(), None, rules, settings, False)
    assert event.state == PrayerState.PRE_ADHAN
    assert resolve_stage(pre, _day(), settings, event, ()) == CountdownOccupant(
        kind="adhan", prayer=MarkerName.DHUHR
    )
    after = datetime(2025, 10, 22, 12, 20, tzinfo=TZ)
    event = resolve_next_event(after, _day(), None, rules, settings, False)
    assert event.state == PrayerState.IQAMAH_COUNTDOWN
    assert resolve_stage(after, _day(), settings, event, ()) == CountdownOccupant(
        kind="iqamah", prayer=MarkerName.DHUHR
    )


@pytest.mark.unit
def test_playlist_value_shapes() -> None:
    item = PlaylistItem(image_path="img/a.jpg", duration_s=10, sort_order=1)
    assert item.duration_s == 10
    playlist = _playlist("a", "09:00", "11:00")
    assert playlist.anchor_marker is None
    assert playlist.anchor_start_offset_min == 0
    assert playlist.anchor_stop_offset_min == 0
    assert playlist.cycle_mode == "indefinite"
    assert playlist.max_cycles is None
    with pytest.raises(FrozenInstanceError):
        playlist.active = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="duration must be positive"):
        PlaylistItem(image_path="img/a.jpg", duration_s=0, sort_order=0)
    with pytest.raises(ValueError, match="needs an image path"):
        PlaylistItem(image_path="", duration_s=10, sort_order=0)
    with pytest.raises(ValueError, match="non-empty id"):
        _playlist("")


def test_stage_id_rejects_unknown_occupant() -> None:
    from muhideen.domain.stage import stage_id

    with pytest.raises(AssertionError, match="unknown stage occupant"):
        stage_id("bogus")  # type: ignore[arg-type]


def test_pre_adhan_window_takes_countdown_stage() -> None:
    from muhideen.domain.stage import CountdownOccupant, resolve_stage

    now = _at(12, 12)
    occupant = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), ())
    assert occupant == CountdownOccupant(kind="adhan", prayer=MarkerName.DHUHR)


def test_exhausted_repeat_playlist_releases_the_stage() -> None:
    from muhideen.domain.stage import ClockOccupant, resolve_stage

    spent = _playlist(
        "spent",
        start="09:00",
        end="10:00",
        cycle_mode="repeat",
        max_cycles=1,
    )
    now = _at(9, 30)
    occupant = resolve_stage(now, _day(), _settings(), _dhuhr_event(now), (spent,))
    assert occupant == ClockOccupant()
