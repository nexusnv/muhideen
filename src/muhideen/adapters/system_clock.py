"""System clock adapter: production Clock over wall + monotonic time."""

from __future__ import annotations

import time as time_mod
from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    """Production ``Clock``: tz-aware ``now`` plus monotonic seconds."""

    def __init__(self, tz: ZoneInfo) -> None:
        """Hold the display IANA timezone for tz-aware reads."""
        self._tz = tz

    def now(self) -> datetime:
        """Return the current wall-clock instant in the display zone."""
        return datetime.now(tz=self._tz)

    def monotonic(self) -> float:
        """Return monotonic seconds unaffected by wall-clock steps."""
        return time_mod.monotonic()
