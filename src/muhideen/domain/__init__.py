"""Domain: pure prayer logic over (now, schedule, settings)."""

from muhideen.domain.fallback import STALE_AFTER, FallbackResult, is_stale, resolve_day
from muhideen.domain.hijri import apply_hijri_offset
from muhideen.domain.iqamah import resolve_iqamah
from muhideen.domain.prayer_state import PRE_ADHAN_WINDOW, resolve_next_event

__all__ = [
    "FallbackResult",
    "PRE_ADHAN_WINDOW",
    "STALE_AFTER",
    "apply_hijri_offset",
    "is_stale",
    "resolve_day",
    "resolve_iqamah",
    "resolve_next_event",
]
