"""Iqamah target resolution: pure delay/fixed computation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, tzinfo

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
    resolved = datetime.combine(
        adhan_at.date(), rule.fixed_time, tzinfo=adhan_at.tzinfo
    )
    if resolved <= adhan_at:
        raise ConfigError(f"fixed iqamah at or before adhan for prayer: {prayer.value}")
    return resolved


def card_iqamah_labels(
    day_date: date,
    times: Mapping[str, str],
    tz: tzinfo | None,
    rules: Mapping[MarkerName, IqamahRule],
    next_prayer: MarkerName | str | None,
) -> dict[str, str]:
    """Per-card iqamah ``HH:MM`` labels; sole owner of delay/fixed semantics.

    ``times`` maps card key (``fajr``/``dhuhr``/``asr``/``maghrib``/``isha``)
    to its adhan ``HH:MM``. The dhuhr card uses the Jumuah rule while
    Jumuah is the upcoming prayer. Every label resolves through
    ``resolve_iqamah``, so the midnight guard and the fixed-time checks
    apply here exactly as they do in the Prayer State machine — the
    display module renders the returned strings and computes nothing.
    """
    next_key = next_prayer.value if isinstance(next_prayer, MarkerName) else next_prayer
    labels: dict[str, str] = {}
    for key, hhmm in times.items():
        rule_key = "jumuah" if (key == "dhuhr" and next_key == "jumuah") else key
        try:
            prayer = MarkerName(rule_key)
        except ValueError:
            raise ConfigError(f"missing iqamah rule for prayer: {rule_key}") from None
        rule = rules.get(prayer)
        if rule is None:
            raise ConfigError(f"missing iqamah rule for prayer: {rule_key}")
        adhan_at = datetime.combine(day_date, time.fromisoformat(hhmm), tzinfo=tz)
        labels[key] = resolve_iqamah(prayer, adhan_at, dict(rules)).strftime("%H:%M")
    return labels
