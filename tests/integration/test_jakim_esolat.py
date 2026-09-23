"""JAKIM e-solat client guards (slice 1A-6, Task 3).

Every client test wires ``httpx.MockTransport`` — zero live URLs, zero
network (``TESTING_STRATEGY.md``: integration = engine + fake sources).
"""

import json
import logging
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from muhideen.core.errors import SyncError
from muhideen.core.ports import JAKIMClient
from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.integration

DATA = Path(__file__).resolve().parents[2] / "tests" / "data"
TZ = ZoneInfo("Asia/Kuala_Lumpur")
PINNED = datetime(2026, 9, 23, 12, 0, tzinfo=TZ)

Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    """File-local pinned clock; production wires adapters/system_clock.py."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 1234.5


def _payload() -> Any:
    return json.loads((DATA / "jakim_year_sgr01.json").read_text())


def _payload_bytes() -> bytes:
    return (DATA / "jakim_year_sgr01.json").read_bytes()


def _parse(payload: Any) -> list[PrayerDay]:
    from muhideen.adapters.jakim_esolat import parse_takwim

    return parse_takwim(payload, zone="SGR01", fetched_at=PINNED)


def _client(handler: Handler, sleeps: list[float]) -> Any:
    from muhideen.adapters.jakim_esolat import HttpJAKIMClient

    return HttpJAKIMClient(
        clock=FakeClock(PINNED),
        sleep=sleeps.append,
        transport=httpx.MockTransport(handler),
    )


# --- parse -----------------------------------------------------------------


def test_parse_year_payload_returns_365_prayer_days() -> None:
    days = _parse(_payload())
    assert len(days) == 365
    assert all(isinstance(d, PrayerDay) for d in days)
    assert days[0].date == date(2026, 1, 1)
    assert days[-1].date == date(2026, 12, 31)


def test_parse_pins_recorded_marker_values_and_provenance() -> None:
    days = _parse(_payload())
    row = next(d for d in days if d.date == date(2026, 9, 23))
    assert row.fajr == time(5, 55)
    assert row.dhuhr == time(13, 9)
    assert row.isha == time(20, 20)
    assert row.source is ScheduleSource.JAKIM
    assert row.fetched_at == PINNED
    assert row.zone == "SGR01"


@pytest.mark.parametrize(
    ("date_text", "expected"),
    [
        ("01-Dis-2026", date(2026, 12, 1)),
        ("15-Ogos-2026", date(2026, 8, 15)),
    ],
)
def test_parse_malay_month_tokens(date_text: str, expected: date) -> None:
    payload = _payload()
    rows = payload["prayerTime"]
    # Swap with the row already carrying this date so the payload keeps its
    # unique full-year coverage (the parser rejects incomplete years).
    holder = next(r for r in rows if r["date"] == date_text)
    rows[0]["date"], holder["date"] = holder["date"], rows[0]["date"]
    days = _parse(payload)
    assert days[0].date == expected


# --- rejection -------------------------------------------------------------


def test_rejects_status_not_ok() -> None:
    payload = _payload()
    payload["status"] = "Zone not found"
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_missing_prayer_time() -> None:
    payload = _payload()
    del payload["prayerTime"]
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_row_missing_marker() -> None:
    payload = _payload()
    del payload["prayerTime"][5]["isha"]
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_empty_prayer_time() -> None:
    # A 200/OK! payload with zero rows must not count as a successful sync.
    payload = _payload()
    payload["prayerTime"] = []
    with pytest.raises(SyncError):
        _parse(payload)


@pytest.mark.parametrize("bad", ["5:55", "25:00", "05:5x"])
def test_rejects_malformed_time(bad: str) -> None:
    payload = _payload()
    payload["prayerTime"][0]["fajr"] = bad
    with pytest.raises(SyncError):
        _parse(payload)


@pytest.mark.parametrize(
    "bad",
    [
        "31-Dez-2026",  # unknown month token
        "2026-09-23",  # wrong shape (not dd-Mmm-yyyy)
        None,  # non-string date
        "01-Jan",  # missing year field
        "1-Jan-2026",  # unpadded day
        "01-Jan-26",  # two-digit year
        "32-Jan-2026",  # impossible calendar day
    ],
)
def test_rejects_malformed_date(bad: str | None) -> None:
    payload = _payload()
    payload["prayerTime"][0]["date"] = bad
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_zone_mismatch() -> None:
    from muhideen.adapters.jakim_esolat import parse_takwim

    with pytest.raises(SyncError):
        parse_takwim(_payload(), zone="KDH01", fetched_at=PINNED)


@pytest.mark.parametrize("period_type", ["week", "month", None])
def test_rejects_non_year_period_type(period_type: str | None) -> None:
    # period=week/month payloads echo their own periodType (research probe
    # table: "week" → 8 rows); accepting one would save a 7-day window as a
    # "successful" year sync and skip the retry chain.
    payload = _payload()
    if period_type is None:
        del payload["periodType"]
    else:
        payload["periodType"] = period_type
    with pytest.raises(SyncError):
        _parse(payload)


@pytest.mark.parametrize("mode", ["truncated", "duplicate_date"])
def test_rejects_incomplete_year_payload(mode: str) -> None:
    # period=year must yield the whole fetched calendar year uniquely —
    # a partial payload would report success while leaving cache holes.
    payload = _payload()
    rows = payload["prayerTime"]
    if mode == "truncated":
        payload["prayerTime"] = rows[:-10]  # Dec 22-31 missing
    else:
        rows[10]["date"] = rows[9]["date"]  # one day twice, another lost
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_out_of_year_row() -> None:
    # Coverage is exactly the fetched year: a row outside it (and the 2026
    # day it displaces) is rejected; dates beyond 31-Dis stay FR-1.2's job.
    payload = _payload()
    payload["prayerTime"][0]["date"] = "01-Jan-2025"
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_ordering_violation() -> None:
    payload = _payload()
    payload["prayerTime"][0]["isha"] = "05:00"
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_non_object_payload() -> None:
    with pytest.raises(SyncError):
        _parse(["nope"])


def test_rejects_row_that_is_not_an_object() -> None:
    payload = _payload()
    payload["prayerTime"][2] = "not-a-row"
    with pytest.raises(SyncError):
        _parse(payload)


def test_rejects_conflicting_marker_spellings() -> None:
    # PRD variant "subuh" maps to the same marker as the observed "fajr".
    payload = _payload()
    payload["prayerTime"][0]["subuh"] = payload["prayerTime"][0]["fajr"]
    with pytest.raises(SyncError):
        _parse(payload)


# --- retry tier 1 ----------------------------------------------------------


def test_transient_500_then_success_records_backoff_sleeps() -> None:
    calls = {"n": 0}
    body = _payload_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=body)

    sleeps: list[float] = []
    days = _client(handler, sleeps).fetch_year("SGR01")
    assert len(days) == 365
    assert sleeps == [2.0, 4.0]
    assert calls["n"] == 3


@pytest.mark.parametrize("mode", ["http500", "connect_timeout", "invalid_json"])
def test_exhausted_failures_raise_sync_error_after_three_retries(
    mode: str,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if mode == "http500":
            return httpx.Response(500)
        if mode == "connect_timeout":
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, text="<html>not json</html>")

    sleeps: list[float] = []
    with pytest.raises(SyncError):
        _client(handler, sleeps).fetch_year("SGR01")
    assert sleeps == [2.0, 4.0, 8.0]
    assert calls["n"] == 4


@pytest.mark.parametrize("bad_status", [404, 429])
def test_4xx_fails_fast_without_in_client_retry(bad_status: int) -> None:
    # 4xx (incl. 429) cannot heal inside 14 s of bursts: the endpoint's WAF
    # ban re-engages within seconds of a burst, so the scheduler tier's
    # 5m/15m/1h spacing is the only retry path for request-level rejections.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(bad_status)

    sleeps: list[float] = []
    with pytest.raises(SyncError):
        _client(handler, sleeps).fetch_year("SGR01")
    assert sleeps == []
    assert calls["n"] == 1


def test_parse_rejection_after_200_does_not_retry() -> None:
    payload = _payload()
    del payload["prayerTime"][0]["isha"]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=payload)

    sleeps: list[float] = []
    with pytest.raises(SyncError):
        _client(handler, sleeps).fetch_year("SGR01")
    assert sleeps == []
    assert calls["n"] == 1


# --- observability ---------------------------------------------------------


def test_failure_logs_zone_and_status(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    client = _client(handler, [])
    with (
        caplog.at_level(logging.WARNING, logger="muhideen.adapters.jakim_esolat"),
        pytest.raises(SyncError),
    ):
        client.fetch_year("SGR01")
    records = [r for r in caplog.records if r.name == "muhideen.adapters.jakim_esolat"]
    assert len(records) == 4
    assert "zone=SGR01" in records[0].getMessage()
    assert "status=500" in records[0].getMessage()


# --- port ------------------------------------------------------------------


def test_http_jakim_client_satisfies_port() -> None:
    from muhideen.adapters.jakim_esolat import HttpJAKIMClient

    assert isinstance(HttpJAKIMClient(clock=FakeClock(PINNED)), JAKIMClient)
