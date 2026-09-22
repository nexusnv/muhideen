"""Iqamah target resolution: pure delay/fixed computation."""

from __future__ import annotations

from datetime import datetime, timedelta

from muhideen.core.errors import ConfigError
from muhideen.core.values import (
    IqamahRule,
    MarkerKind,
    MarkerName,
    marker_kind,
)


def resolve_iqamah(
    prayer: MarkerName,
    adhan_at: datetime,
    rules: dict[MarkerName, IqamahRule],
) -> datetime:
    """Iqamah target for one Prayer Time Marker adhan.

    Boundary Time Markers raise ``ConfigError`` (a ``Settings`` construction
    guard prevents such rules from ever existing).
    """
    if marker_kind(prayer) is MarkerKind.BOUNDARY:
        raise ConfigError(f"boundary time marker has no iqamah: {prayer.value}")
    rule = rules.get(prayer)
    if rule is None:
        raise ConfigError(f"missing iqamah rule for prayer: {prayer.value}")
    if rule.mode == "delay":
        return adhan_at + timedelta(minutes=rule.delay_minutes)
    if rule.fixed_time is None:
        raise ConfigError(f"fixed iqamah rule without time for prayer: {prayer.value}")
    return datetime.combine(adhan_at.date(), rule.fixed_time, tzinfo=adhan_at.tzinfo)
