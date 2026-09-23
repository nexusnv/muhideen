"""Core ports: abstract interfaces the domain and engine depend on.

Concrete adapters live in `adapters/`; in-memory fakes serve tests.
All protocols are runtime-checkable so registries can verify wiring.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from muhideen.core.values import PrayerDay, Settings


@runtime_checkable
class PrayerRepo(Protocol):
    def get_day(self, day: date, zone: str) -> PrayerDay | None: ...

    def save_day(self, prayer_day: PrayerDay) -> None: ...

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        """Most recent saved day with ``date <= day`` for ``zone``, else ``None``."""
        ...


@runtime_checkable
class SettingsRepo(Protocol):
    def load(self) -> Settings: ...

    def save(self, settings: Settings) -> None: ...


@runtime_checkable
class DisplayRepo(Protocol):
    def record_seen(self, display_id: str, ip: str | None) -> None:
        """Buffer one heartbeat for a display.

        Writes are batched (never per-heartbeat write-through, PRD §5.2
        power-cut safety); unregistered IDs are dropped at flush.
        """
        ...

    def flush(self) -> int:
        """Write all buffered heartbeats in one short transaction.

        Returns the number of rows updated.
        """
        ...


@runtime_checkable
class JAKIMClient(Protocol):
    def fetch_year(self, zone: str) -> list[PrayerDay]:
        """One ``period=year`` fetch: the whole calendar year, ~365 rows."""
        ...


@runtime_checkable
class CalcEngine(Protocol):
    def compute_day(
        self, day: date, lat: float, lon: float, method: str
    ) -> PrayerDay: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: str) -> None: ...


@runtime_checkable
class MediaStore(Protocol):
    def list_enabled(self) -> list[str]: ...
