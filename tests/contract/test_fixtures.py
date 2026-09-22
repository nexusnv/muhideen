"""Skeleton: checked-in fixtures parse and carry the contract shapes.

Full DTO-vs-fixture parity belongs to slice 1A-3; these smoke tests only
pin that the fixture files exist and hold the documented keys.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


@pytest.mark.contract
def test_next_event_fixture_shape() -> None:
    payload = json.loads((FIXTURES / "next-event.json").read_text())
    assert set(payload) >= {
        "state",
        "next_prayer",
        "adhan_at",
        "iqamah_at",
        "dim_until",
        "stale",
    }


@pytest.mark.contract
def test_prayer_day_fixture_shape() -> None:
    payload = json.loads((FIXTURES / "prayer-day.json").read_text())
    assert set(payload["times"]) == {
        "fajr",
        "syuruq",
        "dhuhr",
        "asr",
        "maghrib",
        "isha",
    }
    assert set(payload) >= {"date", "zone", "source", "stale"}


@pytest.mark.contract
def test_events_stream_fixture_non_empty() -> None:
    lines = (FIXTURES / "events-stream.txt").read_text().splitlines()
    assert any(line.startswith("event:") for line in lines)
    assert any(line.startswith("data:") for line in lines)
