"""Dim precedence: per-display pin, else group pin, else the default.

Pure rule over already-fetched stored values: the route module reads the
``display_settings`` row and the group pin through the repository ports,
this module owns the precedence, the integer parsing, and the 5–60 range
rule. Invalid stored rows raise ``ConfigError`` so the caller renders its
503 slate; unknown ids resolve to ``(default, "settings")``.
"""

from __future__ import annotations

from muhideen.core.errors import ConfigError

DIM_MIN_MINUTES = 5
DIM_MAX_MINUTES = 60


def effective_dim(
    *,
    display_raw: str | None,
    group_raw: str | None,
    default: int,
) -> tuple[int, str]:
    """Effective dim minutes plus which pin won: display, group, settings.

    ``display_raw`` is the ``dim_minutes_override`` stored string for one
    display id (``None`` when absent); ``group_raw`` is its group's pin
    (``None`` when the display has no group pin — callers skip the group
    read while a display pin exists). Non-integer or out-of-range stored
    values raise ``ConfigError`` whichever pin carried them.
    """
    if display_raw is not None:
        return _parse(display_raw, "display")
    if group_raw is not None:
        return _parse(group_raw, "group")
    return default, "settings"


def _parse(raw: str, source: str) -> tuple[int, str]:
    """Parse one stored pin; raise ``ConfigError`` when it is corrupt."""
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid dim override: {raw!r}") from None
    if not DIM_MIN_MINUTES <= minutes <= DIM_MAX_MINUTES:
        raise ConfigError(f"dim override out of range 5-60: {raw!r}")
    return minutes, source
