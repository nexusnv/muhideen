"""Playlist window grammar matrix: single seam owns HH:MM/marker/None."""

from datetime import date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.values import (
    MarkerName,
    Playlist,
    PlaylistItem,
    PrayerDay,
    ScheduleSource,
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


def _playlist(**overrides: object) -> Playlist:
    base: dict[str, object] = {
        "id": "p",
        "title": "p",
        "active": True,
        "items": (PlaylistItem(image_path="a.jpg", duration_s=10, sort_order=0),),
    }
    base.update(overrides)
    return Playlist(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_parse_clock_bound() -> None:
    from muhideen.domain.playlist_window import parse_bound

    bound = parse_bound("09:00")
    assert bound is not None and bound.kind == "clock"
    assert bound.clock == dtime(9, 0)


@pytest.mark.unit
def test_parse_marker_bound_case_insensitive_and_stripped() -> None:
    from muhideen.domain.playlist_window import parse_bound

    bound = parse_bound("  ASR  ")
    assert bound is not None and bound.kind == "marker"
    assert bound.marker is MarkerName.ASR


@pytest.mark.unit
def test_parse_none_stays_open() -> None:
    from muhideen.domain.playlist_window import parse_bound, parse_window

    assert parse_bound(None) is None
    typed = parse_window(_playlist(window_start=None, window_end=None))
    assert typed.start is None and typed.end is None and typed.anchor is None


@pytest.mark.unit
def test_parse_rejects_unknown_and_boundary_anchor() -> None:
    from muhideen.domain.playlist_window import parse_bound, parse_window

    with pytest.raises(ValueError, match="not HH:MM or marker"):
        parse_bound("bogus")
    with pytest.raises(ValueError, match="not HH:MM or marker"):
        parse_bound("25:99")
    with pytest.raises(ValueError, match="boundary marker cannot anchor"):
        parse_window(_playlist(anchor_marker=MarkerName.SYURUQ))
    with pytest.raises(ValueError, match="window_start"):
        parse_window(_playlist(window_start="bogus"))


@pytest.mark.unit
def test_inverted_spans_midnight() -> None:
    from muhideen.domain.playlist_window import (
        is_in_window,
        parse_window,
        window_datetimes,
    )

    typed = parse_window(_playlist(window_start="22:00", window_end="02:00"))
    day = _day()
    night = datetime(2025, 10, 22, 23, 0, tzinfo=TZ)
    start, stop = window_datetimes(typed, day, night)
    assert start is not None and stop is not None and stop <= start
    assert is_in_window(start, stop, night) is True
    assert is_in_window(start, stop, datetime(2025, 10, 22, 1, 0, tzinfo=TZ)) is True
    assert is_in_window(start, stop, datetime(2025, 10, 22, 3, 0, tzinfo=TZ)) is False


@pytest.mark.unit
def test_anchor_mode_ignores_clock_bounds() -> None:
    from muhideen.domain.playlist_window import parse_window, window_datetimes

    typed = parse_window(
        _playlist(
            window_start="09:00",
            window_end="10:00",
            anchor_marker=MarkerName.MAGHRIB,
            anchor_start_offset_min=-60,
            anchor_stop_offset_min=30,
        )
    )
    assert typed.anchor is MarkerName.MAGHRIB
    start, stop = window_datetimes(
        typed, _day(), datetime(2025, 10, 22, 17, 30, tzinfo=TZ)
    )
    assert start == datetime(2025, 10, 22, 17, 5, tzinfo=TZ)
    assert stop == datetime(2025, 10, 22, 18, 35, tzinfo=TZ)
