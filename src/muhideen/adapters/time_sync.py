"""System time-sync probe: timedatectl first, chrony tracking fallback (FR-1.6).

Answers "does the system clock agree with a network time source?" for the
`TIME UNSYNCED` banner. Both tools are read-only and cheap; a 30s TTL cache
keeps the per-tick engine stamp off the subprocess path. Every failure mode
— neither tool installed, either tool failing, unparseable output — fails
closed to `False`: an unsynced clock must never claim to be synced.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

from muhideen.core.ports import Clock

_TIMECTL_CMD = ["timedatectl", "show", "--property=NTPSynchronized", "--value"]
_CHRONYC_CMD = ["chronyc", "tracking"]
_TTL_S = 30.0
_TIMEOUT_S = 10.0


def _run(cmd: list[str]) -> str:
    """Default runner: one subprocess capture; raises OSError/CalledProcessError."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True, timeout=_TIMEOUT_S
    )
    return result.stdout


class SystemTimeSyncProbe:
    """``TimeSyncProbe`` over timedatectl/chrony with a monotonic TTL cache.

    ``timedatectl`` answers ``yes``/``no`` on ``NTPSynchronized``; when it
    is missing, fails, or answers anything else, ``chronyc tracking`` is the
    fallback and reports synced iff its leap status is ``Normal``. The TTL
    is measured on the injected ``Clock.monotonic()`` so wall-clock steps
    cannot extend or shorten the cache window.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        runner: Callable[[list[str]], str] | None = None,
        ttl_s: float = _TTL_S,
    ) -> None:
        self._clock = clock
        self._runner: Callable[[list[str]], str] = runner if runner else _run
        self._ttl_s = ttl_s
        self._cached: bool | None = None
        self._cached_at = float("-inf")
        self._refresh_lock = threading.Lock()

    def synchronized(self) -> bool:
        """Fresh cached answer inside the TTL; otherwise re-probe the system.

        Check-and-refresh runs under a lock so concurrent callers arriving
        after an expiry share the single in-flight subprocess instead of
        racing ``timedatectl``/``chronyc`` spawns (each up to a 10s
        timeout). Blocking here is safe by construction: every caller is a
        worker thread — the SSE generator offloads ``next_event`` via
        ``asyncio.to_thread``, endpoints run in starlette's threadpool, and
        the ticker is its own thread — so no wait can reach the event loop.
        """
        now = self._clock.monotonic()
        with self._refresh_lock:
            if self._cached is not None and now - self._cached_at < self._ttl_s:
                return self._cached
            value = self._read()
            self._cached = value
            self._cached_at = now
            return value

    def _read(self) -> bool:
        try:
            out = self._runner(_TIMECTL_CMD).strip().lower()
        except (OSError, subprocess.SubprocessError):
            out = ""  # timedatectl unusable: fall through to chrony
        if out in ("yes", "no"):
            return out == "yes"
        try:
            tracking = self._runner(_CHRONYC_CMD)
        except (OSError, subprocess.SubprocessError):
            return False  # fail closed: no usable time tool
        return self._chrony_synced(tracking)

    @staticmethod
    def _chrony_synced(tracking: str) -> bool:
        for line in tracking.splitlines():
            if "Leap status" in line:
                return line.rsplit(":", 1)[-1].strip() == "Normal"
        return False
