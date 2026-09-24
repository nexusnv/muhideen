"""SystemClock adapter: tz-aware now + monotonic (slice 1A-7, Task 1)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muhideen.adapters.system_clock import SystemClock

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kuala_Lumpur")


def test_now_returns_tz_aware_datetime_in_injected_tz() -> None:
    clock = SystemClock(TZ)
    now = clock.now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(tz=TZ).utcoffset()


def test_monotonic_returns_float_seconds() -> None:
    clock = SystemClock(TZ)
    first = clock.monotonic()
    second = clock.monotonic()
    assert isinstance(first, float)
    assert second >= first
