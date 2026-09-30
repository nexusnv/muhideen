"""Domain: pure prayer logic over (now, schedule, settings)."""

from muhideen.domain.countdown import countdown_window
from muhideen.domain.dim import effective_dim
from muhideen.domain.fallback import STALE_AFTER, FallbackResult, is_stale, resolve_day
from muhideen.domain.hijri import apply_hijri_offset
from muhideen.domain.iqamah import card_iqamah_labels, resolve_iqamah
from muhideen.domain.ordering import ORDER, ensure_ordered
from muhideen.domain.prayer_state import resolve_next_event

__all__ = [
    "FallbackResult",
    "ORDER",
    "STALE_AFTER",
    "apply_hijri_offset",
    "card_iqamah_labels",
    "countdown_window",
    "effective_dim",
    "ensure_ordered",
    "is_stale",
    "resolve_day",
    "resolve_iqamah",
    "resolve_next_event",
]
