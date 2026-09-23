"""Contract parity: SSE sample stream vs SSE payload DTOs (slice 1A-3).

`api/fixtures/events-stream.txt` is the normative sample for
`GET /api/events` (`docs/api-contract.md:39-41`): every block must be a
well-formed SSE frame whose `event` name is one of the three documented
events and whose `data` validates against that event's payload DTO.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from muhideen.api.dto import (
    SSE_PAYLOAD_MODELS,
    ConfigUpdateEventDTO,
    StateEventDTO,
    TickEventDTO,
)
from muhideen.core.values import MarkerName, NextEvent, PrayerState

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"
KL = timezone(timedelta(hours=8))
EVENT_NAMES = {"state", "tick", "config-update"}


def _blocks() -> list[list[str]]:
    text = (FIXTURES / "events-stream.txt").read_text()
    return [block.splitlines() for block in text.split("\n\n") if block.strip("\n")]


def _event_name(lines: list[str]) -> str:
    names = [
        line.removeprefix("event:").strip()
        for line in lines
        if line.startswith("event:")
    ]
    return names[0]


def _payload(lines: list[str]) -> Any:
    data = [
        line.removeprefix("data:").strip() for line in lines if line.startswith("data:")
    ]
    return json.loads(data[0])


def test_events_stream_blocks_well_formed() -> None:
    blocks = _blocks()
    assert blocks, "events-stream.txt must contain at least one SSE block"
    for lines in blocks:
        assert sum(line.startswith("event:") for line in lines) == 1
        assert sum(line.startswith("data:") for line in lines) == 1
        assert _event_name(lines) in EVENT_NAMES


def test_events_stream_payloads_validate() -> None:
    for lines in _blocks():
        model = SSE_PAYLOAD_MODELS[_event_name(lines)]
        model.model_validate(_payload(lines))


def test_stream_covers_all_three_event_names() -> None:
    names = {_event_name(lines) for lines in _blocks()}
    assert names == EVENT_NAMES


def test_state_event_omitted_targets_dump_without_null_keys() -> None:
    event = StateEventDTO(
        state="PRE_ADHAN",
        next_prayer="dhuhr",
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=KL),
    )
    dump = event.model_dump(mode="json", exclude_none=True)
    assert set(dump) == {"state", "next_prayer", "adhan_at"}


def test_state_event_includes_boundary_targets_when_set() -> None:
    event = StateEventDTO(
        state="PRE_ADHAN",
        next_prayer="dhuhr",
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=KL),
        next_boundary="imsak",
        boundary_at=datetime(2025, 10, 21, 5, 35, tzinfo=KL),
    )
    dump = event.model_dump(mode="json", exclude_none=True)
    assert set(dump) == {
        "state",
        "next_prayer",
        "adhan_at",
        "next_boundary",
        "boundary_at",
    }


def test_state_event_from_domain_excludes_absent_targets() -> None:
    next_event = NextEvent(
        state=PrayerState.PRE_ADHAN,
        now=datetime(2025, 10, 20, 12, 10, tzinfo=KL),
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=KL),
        iqamah_at=None,
        dim_until=None,
        stale=False,
        next_boundary=None,
        boundary_at=None,
    )
    dump = StateEventDTO.from_domain(next_event).model_dump(
        mode="json", exclude_none=True
    )
    assert "iqamah_at" not in dump
    assert "dim_until" not in dump
    assert "next_boundary" not in dump
    assert "boundary_at" not in dump
    assert dump["state"] == "PRE_ADHAN"
    assert dump["next_prayer"] == "dhuhr"


def test_tick_event_from_domain_carries_now_and_state() -> None:
    next_event = NextEvent(
        state=PrayerState.NORMAL,
        now=datetime(2025, 10, 20, 11, 45, tzinfo=KL),
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=KL),
        iqamah_at=datetime(2025, 10, 20, 12, 30, tzinfo=KL),
        dim_until=datetime(2025, 10, 20, 12, 50, tzinfo=KL),
        stale=False,
        next_boundary=None,
        boundary_at=None,
    )
    assert TickEventDTO.from_domain(next_event).model_dump(mode="json") == {
        "now": "2025-10-20T11:45:00+08:00",
        "state": "NORMAL",
    }


def test_config_update_requires_non_empty_changed_list() -> None:
    with pytest.raises(ValidationError):
        ConfigUpdateEventDTO.model_validate({})
    with pytest.raises(ValidationError):
        ConfigUpdateEventDTO.model_validate({"changed": []})
