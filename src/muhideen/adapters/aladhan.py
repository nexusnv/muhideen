"""Aladhan-compatible timetable client: monthly calendars fetched as one year.

One implementation serves every Aladhan-core host — ``api.aladhan.com``,
``aladhan.api.islamic.network``, or any other mirror — through the
configurable ``aladhan_base_url`` (verified live: both hosts above serve
identical ``/v1`` payloads). Only the base URL varies; the calendar shape,
``method``/``school`` arguments, and ``"HH:MM (+08)"`` timings are the
shared core.

Endpoint per month (``school`` follows this installation's asr juristic,
``method`` defaults to 17/JAKIM so Malaysian numbers stay close to the
e-solat table)::

    GET {base}/calendar/{YYYY}/{M}?latitude={lat}&longitude={lon}&method={m}&school={s}

Retry/error semantics mirror :mod:`muhideen.adapters.jakim_esolat`:
transport-level failures retry with backoff (2s/4s/8s) per month, any 4xx
fails fast (429 stays transient for the scheduler's 5m/15m/1h/6h chain,
every other 4xx never retries), and malformed payloads reject immediately
with zero writes. Twelve monthly calls make one year: a single bad month
fails the whole sync, so the cache is never half-overwritten.

Aladhan serves no Dhuha marker: Dhuha derives as Sunrise plus the
configured ``dhuha_offset_min`` (same offset the calc engine uses), and
the domain ordering check still guards the result.
"""

from __future__ import annotations

import calendar as calendar_mod
import logging
import re
import time as time_mod
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import cast

import httpx

from muhideen.core.errors import ConfigError, SyncError
from muhideen.core.ports import Clock
from muhideen.core.values import MarkerName, PrayerDay, ScheduleSource, Settings
from muhideen.domain import ensure_ordered

logger = logging.getLogger(__name__)

USER_AGENT = "muhideen/0.1.0 (+https://github.com/nexusnv/muhideen)"
TIMEOUT_S = 15.0
RETRY_DELAYS_S: tuple[float, ...] = (2.0, 4.0, 8.0)

DEFAULT_METHOD = 17
"""Aladhan method id for JAKIM (Jabatan Kemajuan Islam Malaysia)."""

SCHOOL_BY_JURISTIC: dict[str, int] = {"shafi": 0, "hanafi": 1}
"""Aladhan ``school`` follows this installation's asr juristic."""

TIMINGS_TO_MARKER: dict[str, MarkerName] = {
    "Imsak": MarkerName.IMSAK,
    "Fajr": MarkerName.FAJR,
    "Sunrise": MarkerName.SYURUQ,
    "Dhuhr": MarkerName.DHUHR,
    "Asr": MarkerName.ASR,
    "Maghrib": MarkerName.MAGHRIB,
    "Isha": MarkerName.ISHA,
}

_TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def _parse_time(value: object, *, day: str, marker: str, zone: str) -> time:
    """Take the ``HH:MM`` head of ``"HH:MM (+08)"`` (bad → SyncError)."""
    if not isinstance(value, str):
        raise SyncError(
            f"aladhan malformed time for {marker} on {day}: {value!r}", zone=zone
        )
    head = value.split(" ", 1)[0]
    if _TIME_RE.fullmatch(head) is None:
        raise SyncError(
            f"aladhan malformed time for {marker} on {day}: {value!r}", zone=zone
        )
    hour_text, minute_text = head.split(":")
    return time(int(hour_text), int(minute_text))


def _parse_gregorian(value: object, *, zone: str) -> date:
    """Parse exactly ``DD-MM-YYYY`` from the calendar day entry (bad → SyncError)."""
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise SyncError(f"aladhan malformed gregorian date: {value!r}", zone=zone)
    day_text, month_text, year_text = value.split("-")
    try:
        return date(int(year_text), int(month_text), int(day_text))
    except ValueError as exc:
        raise SyncError(
            f"aladhan malformed gregorian date: {value!r}", zone=zone
        ) from exc


def parse_calendar_month(
    payload: object,
    *,
    year: int,
    month: int,
    zone: str,
    dhuha_offset_min: int,
    fetched_at: datetime,
) -> list[PrayerDay]:
    """Validate one ``/calendar/{year}/{month}`` payload into ordered days.

    Raises ``SyncError`` on any malformed shape, missing timing, bad value,
    out-of-month date, short/long month, or ordering violation — the caller
    keeps its cache untouched.
    """
    if not isinstance(payload, dict):
        raise SyncError("aladhan payload is not a JSON object", zone=zone)
    body = cast(dict[str, object], payload)
    if body.get("code") != 200 or body.get("status") != "OK":
        raise SyncError(
            f"aladhan unexpected envelope: code={body.get('code')!r} "
            f"status={body.get('status')!r}",
            zone=zone,
        )
    raw_days: object = body.get("data")
    if not isinstance(raw_days, list) or not raw_days:
        raise SyncError("aladhan data is missing or empty — nothing to sync", zone=zone)

    expected = calendar_mod.monthrange(year, month)[1]
    days: list[PrayerDay] = []
    for raw_entry in raw_days:
        if not isinstance(raw_entry, dict):
            raise SyncError("aladhan day is not a JSON object", zone=zone)
        entry = cast(dict[str, object], raw_entry)
        timings = entry.get("timings")
        if not isinstance(timings, dict):
            raise SyncError("aladhan day has no timings object", zone=zone)
        day_entry = entry.get("date")
        gregorian = (
            day_entry.get("gregorian", {}).get("date")
            if isinstance(day_entry, dict)
            else None
        )
        row_date = _parse_gregorian(gregorian, zone=zone)
        if row_date.year != year or row_date.month != month:
            raise SyncError(
                f"aladhan day out of month: {row_date.isoformat()} "
                f"!= {year}-{month:02d}",
                zone=zone,
            )
        day_label = row_date.isoformat()
        times: dict[MarkerName, time] = {}
        for key, marker in TIMINGS_TO_MARKER.items():
            if key not in timings:
                raise SyncError(
                    f"aladhan day {day_label} missing timing: {key}", zone=zone
                )
            times[marker] = _parse_time(
                timings[key], day=day_label, marker=key, zone=zone
            )
        dhuha = (
            datetime.combine(row_date, times[MarkerName.SYURUQ])
            + timedelta(minutes=dhuha_offset_min)
        ).time()
        prayer_day = PrayerDay(
            date=row_date,
            zone=zone,
            imsak=times[MarkerName.IMSAK],
            fajr=times[MarkerName.FAJR],
            syuruq=times[MarkerName.SYURUQ],
            dhuha=dhuha,
            dhuhr=times[MarkerName.DHUHR],
            asr=times[MarkerName.ASR],
            maghrib=times[MarkerName.MAGHRIB],
            isha=times[MarkerName.ISHA],
            source=ScheduleSource.ALADHAN,
            fetched_at=fetched_at,
        )
        days.append(ensure_ordered(prayer_day))

    dates = [day.date for day in days]
    if len(dates) != expected or len(set(dates)) != expected:
        raise SyncError(
            f"aladhan month {year}-{month:02d} does not cover all {expected} days",
            zone=zone,
        )
    return days


class AladhanClient:
    """``fetch_year`` over any Aladhan-compatible ``/v1`` host.

    Coordinates, juristic, and Dhuha offset come from the passed
    ``Settings`` on every call (fresh under hot-reload); only the host
    and calculation method are constructor config (changing them needs a
    restart, like the client wiring itself). Missing coordinates are
    ``ConfigError`` (setup incomplete — the scheduler skips without
    retrying); transport and payload failures are ``SyncError``.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        base_url: str = "https://api.aladhan.com/v1",
        method: int = DEFAULT_METHOD,
        sleep: Callable[[float], None] = time_mod.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Hold the clock, host, method, sleep recorder, and transport."""
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._method = method
        self._sleep = sleep
        self._transport = transport

    def _month_url(
        self, year: int, month: int, lat: float, lon: float, school: int
    ) -> str:
        """One calendar URL; floats cannot inject (formatted as numbers)."""
        return (
            f"{self._base_url}/calendar/{year}/{month}"
            f"?latitude={lat}&longitude={lon}"
            f"&method={self._method}&school={school}"
        )

    def _fetch_month(
        self,
        client: httpx.Client,
        *,
        year: int,
        month: int,
        zone: str,
        lat: float,
        lon: float,
        school: int,
        dhuha_offset_min: int,
        fetched_at: datetime,
    ) -> list[PrayerDay]:
        """GET one month with backoff; 4xx fails fast (429 transient)."""
        url = self._month_url(year, month, lat, lon, school)
        last: Exception | None = None
        for attempt in range(1 + len(RETRY_DELAYS_S)):
            try:
                response = client.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT_S,
                )
                response.raise_for_status()
                payload: object = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                status: object = "n/a"
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                logger.warning(
                    "aladhan fetch failed zone=%s month=%d status=%s attempt=%d: %s",
                    zone,
                    month,
                    status,
                    attempt + 1,
                    exc,
                )
                last = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and 400 <= exc.response.status_code < 500
                ):
                    raise SyncError(
                        f"aladhan fetch rejected with HTTP {status}",
                        zone=zone,
                        transient=(exc.response.status_code == 429),
                    ) from exc
                if attempt < len(RETRY_DELAYS_S):
                    self._sleep(RETRY_DELAYS_S[attempt])
                continue
            return parse_calendar_month(
                payload,
                year=year,
                month=month,
                zone=zone,
                dhuha_offset_min=dhuha_offset_min,
                fetched_at=fetched_at,
            )
        raise SyncError(
            f"aladhan fetch failed after {1 + len(RETRY_DELAYS_S)} attempts "
            f"(month {month})",
            zone=zone,
        ) from last

    def fetch_year(self, settings: Settings) -> list[PrayerDay]:
        """Fetch twelve months and validate the whole year (``SyncError`` on reject)."""
        zone = settings.zone
        if settings.lat is None or settings.lon is None:
            raise ConfigError(
                "aladhan provider needs lat and lon set together",
            )
        school = SCHOOL_BY_JURISTIC[settings.asr_juristic]
        year = self._clock.now().year
        fetched_at = self._clock.now()
        days: list[PrayerDay] = []
        with httpx.Client(transport=self._transport) as client:
            for month in range(1, 13):
                days.extend(
                    self._fetch_month(
                        client,
                        year=year,
                        month=month,
                        zone=zone,
                        lat=settings.lat,
                        lon=settings.lon,
                        school=school,
                        dhuha_offset_min=settings.dhuha_offset_min,
                        fetched_at=fetched_at,
                    )
                )
        expected = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        dates = [day.date for day in days]
        if (
            len(dates) != expected
            or len(set(dates)) != expected
            or any(day_date.year != year for day_date in dates)
        ):
            raise SyncError(f"aladhan year payload does not cover {year}", zone=zone)
        return days
