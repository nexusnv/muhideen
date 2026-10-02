"""Service log reader: journalctl tail for the admin UI (issue #41).

Reads the ``muhideen`` systemd unit's journal via ``journalctl``. No journal
(dev machines, probes failing) is a normal outcome, not an error: callers get
``{"available": False, "hint": ...}`` and must never see a 500 for absent
logs. The subprocess runner is injectable so tests and routes can substitute
a double, mirroring ``time_sync.SystemTimeSyncProbe``.

``--output=json`` emits one JSON object per journal entry, so the ``lines``
bound counts entries rather than raw text lines: an entry whose ``MESSAGE``
contains embedded newlines is still one entry. Unparseable lines are
skipped. ``--quiet`` suppresses journalctl's informational chatter so an
empty journal reads as empty stdout.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any, cast

_UNIT = "muhideen"
_JOURNALCTL = [
    "journalctl",
    "-u",
    _UNIT,
    "--no-pager",
    "--quiet",
    "--output=json",
]
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

    Return ``{"available": True, "lines": [...]}`` with the last ``lines``
    journal entries' messages on success, or ``{"available": False, ...}``
    when journalctl is missing, fails, or reports no entries.
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
    if not output.strip():
        return {
            "available": False,
            "hint": f"journalctl -u {_UNIT} returned no entries",
        }
    entries: list[str] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parsed: Any = None
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        message: Any = (
            cast(dict[str, Any], parsed).get("MESSAGE")
            if isinstance(parsed, dict)
            else None
        )
        if isinstance(message, str):
            entries.append(message)
    return {"available": True, "lines": entries[-count:]}
