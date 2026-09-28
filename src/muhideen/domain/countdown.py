"""Pre-adhan countdown window: effective takeover minutes before adhan.

Pure rule over (settings, prayer): the per-prayer override wins when present,
otherwise the global default applies. Used by the Main Stage engine to decide
when the countdown occupies the Stage ahead of each adhan.
"""

from __future__ import annotations

from muhideen.core.values import MarkerName, Settings


def countdown_window(settings: Settings, prayer: MarkerName) -> int:
    """Effective pre-adhan window in minutes for one Prayer Time Marker."""
    return settings.countdown_before_adhan_overrides.get(
        prayer.value, settings.countdown_before_adhan_min
    )
