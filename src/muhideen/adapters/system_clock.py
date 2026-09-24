"""System clock adapter: production Clock over wall + monotonic time."""

from __future__ import annotations

import time as time_mod
from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    """Production ``Clock``: tz-aware ``now`` plus monotonic seconds."""

    def __init__(self, tz: ZoneInfo) -> None:
        self._tz = tz

    def now(self) -> datetime:
        return datetime.now(tz=self._tz)

    def monotonic(self) -> float:
        return time_mod.monotonic()
