"""Determinism proof (slice 1A-2, Task 6): same inputs, identical output."""

from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from muhideen.core.values import (
    IqamahRule,
    MarkerName,
    PrayerDay,
    PrayerState,
    ScheduleSource,
    Settings,
)

TZ = ZoneInfo("Asia/Kuala_Lumpur")


class FakeClock:
    """File-local pinned clock."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 7.0


def _snapshot() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 20),
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
        fetched_at=datetime(2025, 10, 20, 1, 0, tzinfo=TZ),
    )


def _rules() -> dict[MarkerName, IqamahRule]:
    return {
        MarkerName.FAJR: IqamahRule(
            prayer=MarkerName.FAJR, mode="delay", delay_minutes=15
        ),
        MarkerName.DHUHR: IqamahRule(
            prayer=MarkerName.DHUHR, mode="delay", delay_minutes=10
        ),
        MarkerName.ASR: IqamahRule(
            prayer=MarkerName.ASR, mode="delay", delay_minutes=10
        ),
        MarkerName.MAGHRIB: IqamahRule(
            prayer=MarkerName.MAGHRIB, mode="delay", delay_minutes=10
        ),
        MarkerName.ISHA: IqamahRule(
            prayer=MarkerName.ISHA, mode="delay", delay_minutes=15
        ),
        MarkerName.JUMUAH: IqamahRule(
            prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=10
        ),
    }


def _settings(boundary_countdown: bool = False) -> Settings:
    return Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        adhan_duration_s=180,
        dim_minutes_default=20,
        dim_minutes_jumuah=45,
        boundary_countdown=boundary_countdown,
    )


@pytest.mark.unit
def test_same_inputs_identical_output() -> None:
    from muhideen.domain import resolve_next_event

    now = FakeClock(datetime(2025, 10, 20, 12, 20, tzinfo=TZ)).now()
    first = resolve_next_event(now, _snapshot(), None, _rules(), _settings(), False)
    second = resolve_next_event(now, _snapshot(), None, _rules(), _settings(), False)
    assert first == second
    assert first.state == PrayerState.IQAMAH_COUNTDOWN
    assert first.next_prayer == MarkerName.DHUHR
    assert first.adhan_at == datetime(2025, 10, 20, 12, 15, tzinfo=TZ)
    assert first.iqamah_at == datetime(2025, 10, 20, 12, 25, tzinfo=TZ)
    assert first.dim_until == datetime(2025, 10, 20, 12, 45, tzinfo=TZ)
    assert first.stale is False


@pytest.mark.unit
def test_fixture_vector_replays_identically() -> None:
    """Replay api/fixtures/next-event.json through the domain twice.

    Fixture implies a 15-minute Dhuhr delay (12:15 -> 12:30) with default
    20-minute dim (12:30 -> 12:50); rules below match that configuration.
    `now` parses to a fixed-offset tzinfo, exactly as the contract serves it.
    """

    import json
    from pathlib import Path

    from muhideen.domain import resolve_next_event

    fixtures = Path(__file__).resolve().parents[2] / "api" / "fixtures"
    day_payload = json.loads((fixtures / "prayer-day.json").read_text())
    event_payload = json.loads((fixtures / "next-event.json").read_text())

    prayers = day_payload["prayers"]
    boundaries = day_payload["boundaries"]
    snapshot = PrayerDay(
        date=date.fromisoformat(day_payload["date"]),
        zone=day_payload["zone"],
        imsak=dtime.fromisoformat(boundaries["imsak"]),
        fajr=dtime.fromisoformat(prayers["fajr"]),
        syuruq=dtime.fromisoformat(boundaries["syuruq"]),
        dhuha=dtime.fromisoformat(boundaries["dhuha"]),
        dhuhr=dtime.fromisoformat(prayers["dhuhr"]),
        asr=dtime.fromisoformat(prayers["asr"]),
        maghrib=dtime.fromisoformat(prayers["maghrib"]),
        isha=dtime.fromisoformat(prayers["isha"]),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime.fromisoformat(event_payload["now"]) - timedelta(hours=11),
    )
    rules = _rules()
    rules[MarkerName.DHUHR] = IqamahRule(
        prayer=MarkerName.DHUHR, mode="delay", delay_minutes=15
    )
    now = datetime.fromisoformat(event_payload["now"])
    settings = _settings(boundary_countdown=True)
    first = resolve_next_event(now, snapshot, None, rules, settings, False)
    second = resolve_next_event(now, snapshot, None, rules, settings, False)
    assert first == second
    assert first.state.name == event_payload["state"]
    assert first.next_prayer is not None
    assert first.next_prayer.value == event_payload["next_prayer"]
    assert first.adhan_at is not None
    assert first.adhan_at.isoformat() == event_payload["adhan_at"]
    assert first.iqamah_at is not None
    assert first.iqamah_at.isoformat() == event_payload["iqamah_at"]
    assert first.dim_until is not None
    assert first.dim_until.isoformat() == event_payload["dim_until"]
    assert first.stale == event_payload["stale"]
    assert first.next_boundary is not None
    assert first.next_boundary.value == event_payload["next_boundary"]
    assert first.boundary_at is not None
    assert first.boundary_at.isoformat() == event_payload["boundary_at"]
