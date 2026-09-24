"""Core ports: abstract interfaces the domain and engine depend on.

Concrete adapters live in `adapters/`; in-memory fakes serve tests.
All protocols are runtime-checkable so registries can verify wiring.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from muhideen.core.values import PrayerDay, Settings


@runtime_checkable
class PrayerRepo(Protocol):
    """Stored prayer-day schedules keyed by date and zone."""

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        """Return the saved day for ``day``/``zone``, else ``None``."""
        ...

    def save_day(self, prayer_day: PrayerDay) -> None:
        """Upsert one prayer day (insert or replace on date+zone conflict)."""
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
class DisplayRepo(Protocol):
    """Heartbeat buffer for pre-registered displays (batched flush)."""

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
    """Year-table fetcher for one JAKIM zone (network lives in adapters)."""

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        """One ``period=year`` fetch: the whole calendar year, ~365 rows."""
        ...


@runtime_checkable
class CalcEngine(Protocol):
    """On-device prayer-time calculator for configured coordinates."""

    def compute_day(self, day: date, lat: float, lon: float, method: str) -> PrayerDay:
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
class MediaStore(Protocol):
    """Enabled media assets for the display carousel (future media slice)."""

    def list_enabled(self) -> list[str]:
        """Return identifiers of enabled media in display order."""
        ...


@runtime_checkable
class UserRepo(Protocol):
    """Single-admin credential store with hashed passwords."""

    def has_users(self) -> bool:
        """Return True once the initial admin has been created."""
        ...

    def create_user(self, username: str, password: str) -> bool:
        """Create a user; return False when the username already exists."""
        ...

    def verify(self, username: str, password: str) -> bool:
        """Return True when the password verifies; False otherwise."""
        ...


@runtime_checkable
class TimeSyncProbe(Protocol):
    """Whether the system clock agrees with a network time source (FR-1.6)."""

    def synchronized(self) -> bool:
        """Return True when the system clock agrees with network time."""
        ...
