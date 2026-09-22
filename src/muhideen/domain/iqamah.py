"""Iqamah target resolution: pure delay/fixed computation."""

from __future__ import annotations

from datetime import datetime, timedelta

from muhideen.core.errors import ConfigError
from muhideen.core.values import IqamahRule, PrayerName


def resolve_iqamah(
    prayer: PrayerName,
    adhan_at: datetime,
    rules: dict[PrayerName, IqamahRule],
) -> datetime | None:
    """Return the iqamah target for one adhan, or None for Syuruq."""
    if prayer is PrayerName.SYURUQ:
        return None
    rule = rules.get(prayer)
    if rule is None:
        raise ConfigError(f"missing iqamah rule for prayer: {prayer.value}")
    if rule.mode == "delay":
        return adhan_at + timedelta(minutes=rule.delay_minutes)
    if rule.fixed_time is None:
        raise ConfigError(f"fixed iqamah rule without time for prayer: {prayer.value}")
    return datetime.combine(adhan_at.date(), rule.fixed_time, tzinfo=adhan_at.tzinfo)
