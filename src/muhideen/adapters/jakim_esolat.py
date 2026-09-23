"""Defensive JAKIM e-solat client: one ``period=year`` fetch per sync (PRD §6.1).

Unofficial, publicly accessible endpoint with no service guarantees — the
client pins UA/timeout, retries transport-level failures with backoff
(2s/4s/8s), fails fast on any 4xx (incl. 429: request-level rejections
cannot heal inside one burst — the endpoint's WAF ban re-engages within
seconds of a burst, so retries happen only at the scheduler tier),
rejects malformed payloads immediately (**no** retry: reject + keep
cache), and logs zone + HTTP status on every failed attempt. Parse
happens fully before anything returns, so a rejected payload performs
zero writes and the existing cache survives.
"""

from __future__ import annotations

import logging
import re
import time as time_mod
from collections.abc import Callable
from datetime import date, datetime, time
from typing import cast
from urllib.parse import quote

import httpx

from muhideen.core.errors import SyncError
from muhideen.core.ports import Clock
from muhideen.core.values import MarkerName, PrayerDay, ScheduleSource
from muhideen.domain import ORDER, ensure_ordered

logger = logging.getLogger(__name__)

URL_TEMPLATE = (
    "https://www.e-solat.gov.my/index.php?"
    "r=esolatApi/takwimsolat&period=year&zone={zone}"
)
USER_AGENT = "muhideen/0.1.0 (+https://github.com/nexusnv/muhideen)"
TIMEOUT_S = 15.0
RETRY_DELAYS_S: tuple[float, ...] = (2.0, 4.0, 8.0)

SOURCE_TO_MARKER: dict[str, MarkerName] = {
    # keys observed in the captured year payloads
    "imsak": MarkerName.IMSAK,
    "fajr": MarkerName.FAJR,
    "syuruk": MarkerName.SYURUQ,
    "dhuha": MarkerName.DHUHA,
    "dhuhr": MarkerName.DHUHR,
    "asr": MarkerName.ASR,
    "maghrib": MarkerName.MAGHRIB,
    "isha": MarkerName.ISHA,
    # PRD §6.1 documented source spellings
    "subuh": MarkerName.FAJR,
    "zohor": MarkerName.DHUHR,
    "isyak": MarkerName.ISHA,
    "duha": MarkerName.DHUHA,
}

MALAY_MONTHS: dict[str, int] = {
    "Jan": 1,
    "Feb": 2,
    "Mac": 3,
    "Apr": 4,
    "Mei": 5,
    "Jun": 6,
    "Jul": 7,
    "Ogos": 8,
    "Sep": 9,
    "Okt": 10,
    "Nov": 11,
    "Dis": 12,
    # English aliases (defensive; the endpoint serves the Malay set only)
    "Mar": 3,
    "May": 5,
    "Aug": 8,
    "Oct": 10,
    "Dec": 12,
}

_TIME_RE = re.compile(r"\d{2}:\d{2}(?::\d{2})?")


def _parse_row_date(value: object, *, zone: str) -> date:
    """Parse exactly ``dd-Mmm-yyyy`` via the Malay month map (bad → SyncError)."""
    if not isinstance(value, str):
        raise SyncError(f"malformed date: {value!r}", zone=zone)
    # Exact shape: rejects ``1-Jan-2026``/``01-Jan-26`` before any conversion
    # (unpadded day or short year would otherwise become a valid PrayerDay).
    if re.fullmatch(r"\d{2}-[A-Za-z]{3,4}-\d{4}", value) is None:
        raise SyncError(f"malformed date: {value!r}", zone=zone)
    day_text, month_token, year_text = value.split("-")
    month = MALAY_MONTHS.get(month_token)
    if month is None:
        raise SyncError(f"unknown month token in date: {value!r}", zone=zone)
    try:
        return date(int(year_text), month, int(day_text))
    except ValueError as exc:
        raise SyncError(f"malformed date: {value!r}", zone=zone) from exc


def _parse_marker_time(marker: MarkerName, value: object, *, zone: str) -> time:
    """Validate ``HH:MM[:SS]`` and build a stdlib ``time`` (bad → SyncError)."""
    if not isinstance(value, str) or _TIME_RE.fullmatch(value) is None:
        raise SyncError(f"malformed time for {marker.value}: {value!r}", zone=zone)
    hour_text, minute_text, *rest = value.split(":")
    try:
        return time(
            int(hour_text),
            int(minute_text),
            int(rest[0]) if rest else 0,
        )
    except ValueError as exc:
        raise SyncError(
            f"out-of-range time for {marker.value}: {value!r}", zone=zone
        ) from exc


def parse_takwim(
    payload: object, *, zone: str, fetched_at: datetime
) -> list[PrayerDay]:
    """Validate one ``period=year`` payload and convert every row.

    Raises ``SyncError`` on any malformed shape, bad marker/date value,
    zone mismatch, missing marker, empty ``prayerTime`` list, or ordering
    violation — the caller keeps its cache untouched (Design Decision 5).
    """
    if not isinstance(payload, dict):
        raise SyncError("payload is not a JSON object", zone=zone)
    body = cast(dict[str, object], payload)

    status: object = body.get("status")
    if not isinstance(status, str) or status.rstrip("!") != "OK":
        raise SyncError(f"unexpected status: {status!r}", zone=zone)

    payload_zone: object = body.get("zone")
    if payload_zone != zone:
        raise SyncError(
            f"zone mismatch: payload {payload_zone!r} != requested {zone!r}",
            zone=zone,
        )

    raw_rows: object = body.get("prayerTime")
    if not isinstance(raw_rows, list):
        raise SyncError("prayerTime is missing or not a list", zone=zone)
    rows = cast(list[object], raw_rows)
    if not rows:
        # 200/OK! with zero rows is a degenerate payload: accepting it would
        # report a successful sync while saving nothing and skipping retries.
        raise SyncError("prayerTime is empty — nothing to sync", zone=zone)

    days: list[PrayerDay] = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise SyncError("row is not a JSON object", zone=zone)
        row = cast(dict[str, object], raw_row)

        times: dict[MarkerName, time] = {}
        for key, value in row.items():
            marker = SOURCE_TO_MARKER.get(key)
            if marker is None:
                continue  # hijri, day, date, unknown extras: not markers
            if marker in times:
                raise SyncError(
                    f"conflicting spellings for marker {marker.value}: {key!r}",
                    zone=zone,
                )
            times[marker] = _parse_marker_time(marker, value, zone=zone)

        row_date = _parse_row_date(row.get("date"), zone=zone)

        missing = [marker.value for marker in ORDER if marker not in times]
        if missing:
            raise SyncError(
                f"row {row_date.isoformat()} missing markers: {', '.join(missing)}",
                zone=zone,
            )

        prayer_day = PrayerDay(
            date=row_date,
            zone=zone,
            imsak=times[MarkerName.IMSAK],
            fajr=times[MarkerName.FAJR],
            syuruq=times[MarkerName.SYURUQ],
            dhuha=times[MarkerName.DHUHA],
            dhuhr=times[MarkerName.DHUHR],
            asr=times[MarkerName.ASR],
            maghrib=times[MarkerName.MAGHRIB],
            isha=times[MarkerName.ISHA],
            source=ScheduleSource.JAKIM,
            fetched_at=fetched_at,
        )
        days.append(ensure_ordered(prayer_day))
    return days


class HttpJAKIMClient:
    """``fetch_year`` over the unofficial endpoint, per PRD §6.1.

    Up to 4 attempts (3 backoff sleeps: 2s/4s/8s) around 5xx, transport
    failures, and invalid JSON; any 4xx (incl. 429) fails fast after the
    first request — request-level rejections cannot heal inside one burst
    and in-client bursts keep the endpoint's WAF ban re-engaged, so the
    scheduler tier (5m/15m/1h, FR-1.1) paces those retries instead. A
    ``SyncError`` from ``parse_takwim`` escapes immediately — a rejected
    payload is never retried.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        sleep: Callable[[float], None] = time_mod.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Hold the injected clock, the sleep recorder (tests), and the transport."""
        self._clock = clock
        self._sleep = sleep
        self._transport = transport

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        """Fetch and fully validate one calendar year; ``SyncError`` on rejection."""
        url = URL_TEMPLATE.format(zone=quote(zone, safe=""))
        last: Exception | None = None
        with httpx.Client(transport=self._transport) as client:
            for attempt in range(1 + len(RETRY_DELAYS_S)):
                payload: object
                try:
                    response = client.get(
                        url,
                        headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT_S,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    status: object = "n/a"
                    if isinstance(exc, httpx.HTTPStatusError):
                        status = exc.response.status_code
                    logger.warning(
                        "jakim fetch failed zone=%s status=%s attempt=%d: %s",
                        zone,
                        status,
                        attempt + 1,
                        exc,
                    )
                    last = exc
                    if (
                        isinstance(exc, httpx.HTTPStatusError)
                        and 400 <= exc.response.status_code < 500
                    ):
                        # 4xx (incl. 429): fail fast — one request per sync
                        # attempt; the scheduler tier's 5m/15m/1h spacing is
                        # the retry path (in-client 2/4/8s bursts re-engage
                        # the endpoint's WAF ban).
                        raise SyncError(
                            f"jakim fetch rejected with HTTP {status}", zone=zone
                        ) from exc
                    if attempt < len(RETRY_DELAYS_S):
                        self._sleep(RETRY_DELAYS_S[attempt])
                    continue
                # A SyncError from parse escapes here: reject, keep cache.
                return parse_takwim(payload, zone=zone, fetched_at=self._clock.now())
        raise SyncError(
            f"jakim fetch failed after {1 + len(RETRY_DELAYS_S)} attempts",
            zone=zone,
        ) from last
