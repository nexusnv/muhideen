"""Core ports: abstract interfaces the domain and engine depend on.

Concrete adapters live in `adapters/`; in-memory fakes serve tests.
All protocols are runtime-checkable so registries can verify wiring.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from muhideen.core.values import AsrJuristic, PrayerDay, Settings


@runtime_checkable
class PrayerRepo(Protocol):
    """Stored prayer-day schedules keyed by date and zone."""

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        """Return the saved day for ``day``/``zone``, else ``None``."""
        ...

    def get_pin(self, day: date, zone: str) -> PrayerDay | None:
        """Return the manual pin completed against the stored row, else None.

        Partial pins complete per-marker against the stored provider row
        (pin markers win); uncompletable partial pins raise ``ConfigError``
        instead of silently dropping the correction. Complete pins ignore
        the stored row. ``None`` means no pin for the date.
        """
        ...

    def save_day(self, prayer_day: PrayerDay) -> None:
        """Upsert one prayer day (insert or replace on date+zone conflict)."""
        ...

    def save_day_unless_manual(self, prayer_day: PrayerDay) -> bool:
        """Upsert unless a manual pin holds the date; True when written.

        The read of the stored row and the conditional write are one
        atomic step: a concurrent manual pin edit between a separate check
        and write must never be clobbered by the daily sync.
        """
        ...

    def delete_day(self, day: date, zone: str) -> bool:
        """Delete the manual pin for ``day``/``zone``.

        Only a row whose source is manual is removed; anything else
        (missing row, automatic source) leaves the table untouched and
        returns False. The match-and-delete is one atomic step.
        """
        ...

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        """Most recent saved day with ``date <= day`` for ``zone``, else ``None``."""
        ...


@runtime_checkable
class SettingsRepo(Protocol):
    """Installed configuration: load raises when setup has not run."""

    def load(self) -> Settings:
        """Load settings; raise ``SettingsNotInitializedError`` before setup."""
        ...

    def save(self, settings: Settings) -> None:
        """Persist settings as one atomic full replacement."""
        ...


@runtime_checkable
class ScheduleClient(Protocol):
    """Year-table fetcher for the configured zone (network lives in adapters).

    ``JAKIMClient`` was renamed when the second implementation landed:
    JAKIM e-solat is one fetcher, Aladhan-compatible APIs are another.
    The whole ``Settings`` goes in (not just the zone) so coordinate-based
    providers read fresh lat/lon/asr-juristic/dhuha-offset on every sync —
    constructor-injected coordinates would go stale under hot-reload.
    """

    def fetch_year(self, settings: Settings) -> list[PrayerDay]:
        """One year fetch: the whole calendar year, ~365 rows."""
        ...


@runtime_checkable
class CalcEngine(Protocol):
    """On-device prayer-time calculator for configured coordinates."""

    def compute_day(
        self,
        day: date,
        lat: float,
        lon: float,
        method: str,
        *,
        imsak_offset_min: int = 10,
        dhuha_offset_min: int = 28,
        asr_juristic: AsrJuristic = "shafi",
    ) -> PrayerDay:
        """Compute one day's markers; raise ``ValueError`` for unknown methods."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Injected time source: wall clock plus monotonic seconds."""

    def now(self) -> datetime:
        """Return the current tz-aware wall-clock instant."""
        ...

    def monotonic(self) -> float:
        """Return monotonic seconds for TTLs, windows, and batching."""
        ...


@runtime_checkable
class EventBus(Protocol):
    """Fan-out channel for state/tick/config-update display events."""

    def publish(self, event: str, changed: Sequence[str] = ()) -> None:
        """Publish ``event``; ``changed`` carries config-update groups."""
        ...


@runtime_checkable
class TimeSyncProbe(Protocol):
    """Whether the system clock agrees with a network time source (FR-1.6)."""

    def synchronized(self) -> bool:
        """Return True when the system clock agrees with network time."""
        ...
