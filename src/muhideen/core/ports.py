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


@runtime_checkable
class SettingsRepo(Protocol):
    def load(self) -> Settings: ...

    def save(self, settings: Settings) -> None: ...


@runtime_checkable
class JAKIMClient(Protocol):
    def fetch_week(self, zone: str) -> list[PrayerDay]: ...


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
