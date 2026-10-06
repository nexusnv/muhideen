"""Aladhan-compatible client guards (schedule-provider slice).

Every client test wires ``httpx.MockTransport`` — zero live URLs, zero
network. Payloads are synthetic but shape-faithful to the live
``/v1/calendar`` envelope (verified against ``api.aladhan.com`` during
development: method 17/JAKIM, ``"HH:MM (+08)"`` timings, ``DD-MM-YYYY``
gregorian dates, no Dhuha marker).
"""

import calendar as calendar_mod
import json as json_mod
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from muhideen.core.errors import ConfigError, SyncError
from muhideen.core.ports import ScheduleClient
from muhideen.core.values import PrayerDay, ScheduleSource, Settings

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")
PINNED = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)
LAT, LON = 3.139, 101.6869

_TIMINGS = {
    "Fajr": "05:43 (+08)",
    "Sunrise": "06:59 (+08)",
    "Dhuhr": "13:02 (+08)",
    "Asr": "16:15 (+08)",
    "Sunset": "19:04 (+08)",
    "Maghrib": "19:04 (+08)",
    "Isha": "20:13 (+08)",
    "Imsak": "05:33 (+08)",
    "Midnight": "01:02 (+08)",
}


class FakeClock:
    """File-local pinned clock; production wires adapters/system_clock.py."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 0.0


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
        "lat": LAT,
        "lon": LON,
    }
    base.update(overrides)
    return Settings(**base)


def _month_payload(
    year: int, month: int, *, timings: dict[str, str] | None = None
) -> dict[str, Any]:
    """One faithful calendar envelope with uniform times every day."""
    days_in_month = calendar_mod.monthrange(year, month)[1]
    return {
        "code": 200,
        "status": "OK",
        "data": [
            {
                "timings": dict(timings or _TIMINGS),
                "date": {
                    "gregorian": {
                        "date": f"{day:02d}-{month:02d}-{year}",
                    },
                },
            }
            for day in range(1, days_in_month + 1)
        ],
    }


def _client(
    handler: Any,
    sleeps: list[float],
    **overrides: Any,
) -> Any:
    from muhideen.adapters.aladhan import AladhanClient

    return AladhanClient(
        clock=FakeClock(PINNED),
        sleep=sleeps.append,
        transport=httpx.MockTransport(handler),
        **overrides,
    )


def _parse(payload: Any) -> list[PrayerDay]:
    from muhideen.adapters.aladhan import parse_calendar_month

    return parse_calendar_month(
        payload,
        year=2026,
        month=10,
        zone="SGR01",
        dhuha_offset_min=28,
        fetched_at=PINNED,
    )


# --- parse ---------------------------------------------------------------


def test_parse_month_returns_ordered_days_with_provenance() -> None:
    days = _parse(_month_payload(2026, 10))
    assert len(days) == 31
    first = days[0]
    assert first.date == date(2026, 10, 1)
    assert first.zone == "SGR01"
    assert first.fajr == time(5, 43)
    assert first.syuruq == time(6, 59)
    assert first.isha == time(20, 13)
    # No Dhuha marker upstream: Sunrise plus the configured offset.
    assert first.dhuha == time(7, 27)
    assert first.source is ScheduleSource.ALADHAN
    assert first.fetched_at == PINNED


def test_parse_rejects_bad_envelope() -> None:
    good = _month_payload(2026, 10)
    for bad in (
        ["nope"],
        {**good, "code": 400},
        {**good, "status": "FAIL"},
        {**good, "data": []},
        {**good, "data": "nope"},
    ):
        with pytest.raises(SyncError):
            _parse(bad)


def test_parse_rejects_missing_timing() -> None:
    payload = _month_payload(2026, 10)
    del payload["data"][0]["timings"]["Isha"]
    with pytest.raises(SyncError, match="Isha"):
        _parse(payload)


@pytest.mark.parametrize("bad", ["5:43", "25:00", "05:4x", None, 543])
def test_parse_rejects_malformed_time(bad: Any) -> None:
    timings = dict(_TIMINGS, Fajr=bad)
    with pytest.raises(SyncError, match="Fajr"):
        _parse(_month_payload(2026, 10, timings=timings))


@pytest.mark.parametrize("bad", ["2026-10-05", "05-10-26", None, "32-10-2026"])
def test_parse_rejects_malformed_gregorian_date(bad: Any) -> None:
    payload = _month_payload(2026, 10)
    payload["data"][0]["date"]["gregorian"]["date"] = bad
    with pytest.raises(SyncError, match="gregorian"):
        _parse(payload)


def test_parse_rejects_out_of_month_day() -> None:
    payload = _month_payload(2026, 10)
    payload["data"][0]["date"]["gregorian"]["date"] = "01-11-2026"
    with pytest.raises(SyncError, match="out of month"):
        _parse(payload)


def test_parse_rejects_short_and_duplicate_month() -> None:
    payload = _month_payload(2026, 10)
    del payload["data"][0]
    with pytest.raises(SyncError, match="does not cover"):
        _parse(payload)
    payload = _month_payload(2026, 10)
    payload["data"][0]["date"]["gregorian"]["date"] = "02-10-2026"
    with pytest.raises(SyncError, match="does not cover"):
        _parse(payload)


def test_parse_rejects_ordering_violation() -> None:
    timings = dict(_TIMINGS, Isha="05:00 (+08)")
    with pytest.raises(SyncError):
        _parse(_month_payload(2026, 10, timings=timings))


def test_parse_rejects_non_object_day() -> None:
    payload = _month_payload(2026, 10)
    payload["data"][0] = 42
    with pytest.raises(SyncError, match="not a JSON object"):
        _parse(payload)


def test_parse_rejects_day_without_timings() -> None:
    payload = _month_payload(2026, 10)
    del payload["data"][0]["timings"]
    with pytest.raises(SyncError, match="no timings"):
        _parse(payload)


def test_parse_rejects_non_object_date() -> None:
    """A string day entry carries no gregorian object — loud, not skipped."""
    payload = _month_payload(2026, 10)
    payload["data"][0]["date"] = "01-10-2026"
    with pytest.raises(SyncError, match="gregorian"):
        _parse(payload)


def test_parse_rejects_non_object_gregorian() -> None:
    payload = _month_payload(2026, 10)
    payload["data"][0]["date"]["gregorian"] = "01-10-2026"
    with pytest.raises(SyncError, match="gregorian"):
        _parse(payload)


# --- fetch_year ----------------------------------------------------------


def _year_handler(
    requests: list[httpx.Request],
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        month = int(str(request.url).split("/calendar/2026/")[1].split("?")[0])
        return httpx.Response(
            200, content=json_mod.dumps(_month_payload(2026, month)).encode()
        )

    return handler


def test_fetch_year_assembles_twelve_months() -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    days = _client(_year_handler(requests), sleeps).fetch_year(_settings())
    assert len(days) == 365
    assert sleeps == []
    assert len(requests) == 12
    first = requests[0]
    params = dict(first.url.params)
    assert params["latitude"] == str(LAT)
    assert params["longitude"] == str(LON)
    assert params["method"] == "17"
    assert params["school"] == "0"
    assert days[0].date == date(2026, 1, 1)
    assert days[-1].date == date(2026, 12, 31)
    assert {day.source for day in days} == {ScheduleSource.ALADHAN}


def test_school_follows_asr_juristic() -> None:
    requests: list[httpx.Request] = []
    _client(_year_handler(requests), []).fetch_year(_settings(asr_juristic="hanafi"))
    assert dict(requests[0].url.params)["school"] == "1"


def test_base_url_selects_mirror_host() -> None:
    requests: list[httpx.Request] = []
    _client(
        _year_handler(requests),
        [],
        base_url="https://aladhan.api.islamic.network/v1/",
    ).fetch_year(_settings())
    assert requests[0].url.host == "aladhan.api.islamic.network"


def test_missing_coordinates_is_config_error() -> None:
    with pytest.raises(ConfigError, match="lat and lon"):
        _client(_year_handler([]), []).fetch_year(_settings(lat=None, lon=None))


def test_4xx_fails_fast_without_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404)

    sleeps: list[float] = []
    with pytest.raises(SyncError) as exc_info:
        _client(handler, sleeps).fetch_year(_settings())
    assert exc_info.value.transient is False
    assert sleeps == []


def test_429_stays_transient_for_scheduler_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429)

    with pytest.raises(SyncError) as exc_info:
        _client(handler, []).fetch_year(_settings())
    assert exc_info.value.transient is True


def test_transport_failures_retry_then_succeed() -> None:
    calls = {"n": 0}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(500)
        month = int(str(request.url).split("/calendar/2026/")[1].split("?")[0])
        return httpx.Response(
            200, content=json_mod.dumps(_month_payload(2026, month)).encode()
        )

    sleeps: list[float] = []
    days = _client(handler, sleeps).fetch_year(_settings())
    assert len(days) == 365
    assert sleeps == [2.0, 4.0]


def test_exhausted_month_raises_sync_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    sleeps: list[float] = []
    with pytest.raises(SyncError, match="failed after"):
        _client(handler, sleeps).fetch_year(_settings())
    assert sleeps == [2.0, 4.0, 8.0]


def test_client_satisfies_port() -> None:
    from muhideen.adapters.aladhan import AladhanClient

    assert isinstance(AladhanClient(clock=FakeClock(PINNED)), ScheduleClient)
