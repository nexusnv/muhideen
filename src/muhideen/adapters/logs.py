"""Service log reader: journalctl tail for the admin UI (issue #41).

Reads the ``muhideen`` systemd unit's journal via ``journalctl``. No journal
(dev machines, probes failing) is a normal outcome, not an error: callers get
``{"available": False, "hint": ...}`` and must never see a 500 for absent
logs. The subprocess runner is injectable so tests and routes can substitute
a double, mirroring ``time_sync.SystemTimeSyncProbe``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

_UNIT = "muhideen"
_JOURNALCTL = ["journalctl", "-u", _UNIT, "--no-pager", "--output=short"]
_TIMEOUT_S = 10.0
_DEFAULT_LINES = 100
_MAX_LINES = 1000


def _run(cmd: list[str]) -> str:
    """Default runner: one subprocess capture; raises OSError/CalledProcessError."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True, timeout=_TIMEOUT_S
    )
    return result.stdout


def read_logs(
    *,
    lines: int = _DEFAULT_LINES,
    runner: Callable[[list[str]], str] | None = None,
) -> dict[str, object]:
    """Tail the service journal; clamp ``lines`` to 1..1000.

    Return ``{"available": True, "lines": [...]}`` on success or
    ``{"available": False, "hint": ...}`` when journalctl is missing or fails.
    """
    count = min(max(int(lines), 1), _MAX_LINES)
    run: Callable[[list[str]], str] = runner if runner is not None else _run
    try:
        output = run([*_JOURNALCTL, "-n", str(count)])
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "hint": f"journalctl -u {_UNIT} unavailable: {exc}",
        }
    return {"available": True, "lines": output.splitlines()}
