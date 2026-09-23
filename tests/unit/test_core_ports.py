"""Guards for core ports (slice 1A-1, Task 3)."""

from datetime import date, datetime
from typing import Any

import pytest

from muhideen.core.ports import (
    CalcEngine,
    Clock,
    DisplayRepo,
    EventBus,
    JAKIMClient,
    MediaStore,
    PrayerRepo,
    SettingsRepo,
)
from muhideen.core.values import PrayerDay, Settings


class FullPrayerRepo:
    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError

    def save_day(self, prayer_day: PrayerDay) -> None:
        raise NotImplementedError

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError


class PartialPrayerRepo:
    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError


class MissingLastKnownRepo:
    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError

    def save_day(self, prayer_day: PrayerDay) -> None:
        raise NotImplementedError


class FullSettingsRepo:
    def load(self) -> Settings:
        raise NotImplementedError

    def save(self, settings: Settings) -> None:
        raise NotImplementedError


class PartialSettingsRepo:
    def load(self) -> Settings:
        raise NotImplementedError


class FullDisplayRepo:
    def record_seen(self, display_id: str, ip: str | None) -> None:
        raise NotImplementedError

    def flush(self) -> int:
        raise NotImplementedError


class PartialDisplayRepo:
    def record_seen(self, display_id: str, ip: str | None) -> None:
        raise NotImplementedError


class FullJAKIMClient:
    def fetch_year(self, zone: str) -> list[PrayerDay]:
        raise NotImplementedError


class PartialJAKIMClient:
    def save_day(self, prayer_day: PrayerDay) -> None:
        raise NotImplementedError


class FullCalcEngine:
    def compute_day(self, day: date, lat: float, lon: float, method: str) -> PrayerDay:
        raise NotImplementedError


class PartialCalcEngine:
    def compute_day_for_zone(self, day: date) -> PrayerDay:
        raise NotImplementedError


class FullClock:
    def now(self) -> datetime:
        raise NotImplementedError

    def monotonic(self) -> float:
        raise NotImplementedError


class PartialClock:
    def now(self) -> datetime:
        raise NotImplementedError


class FullEventBus:
    def publish(self, event: str) -> None:
        raise NotImplementedError


class PartialEventBus:
    def emit(self, event: str) -> None:
        raise NotImplementedError


class FullMediaStore:
    def list_enabled(self) -> list[str]:
        raise NotImplementedError


class PartialMediaStore:
    def list_all(self) -> list[str]:
        raise NotImplementedError


ALL_PROTOCOLS = (
    PrayerRepo,
    SettingsRepo,
    DisplayRepo,
    JAKIMClient,
    CalcEngine,
    Clock,
    EventBus,
    MediaStore,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stub", "protocol"),
    [
        (FullPrayerRepo(), PrayerRepo),
        (FullSettingsRepo(), SettingsRepo),
        (FullDisplayRepo(), DisplayRepo),
        (FullJAKIMClient(), JAKIMClient),
        (FullCalcEngine(), CalcEngine),
        (FullClock(), Clock),
        (FullEventBus(), EventBus),
        (FullMediaStore(), MediaStore),
    ],
)
def test_satisfying_stub_passes_isinstance(stub: Any, protocol: type) -> None:
    assert isinstance(stub, protocol)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stub", "protocol"),
    [
        (PartialPrayerRepo(), PrayerRepo),
        (MissingLastKnownRepo(), PrayerRepo),
        (PartialSettingsRepo(), SettingsRepo),
        (PartialDisplayRepo(), DisplayRepo),
        (PartialJAKIMClient(), JAKIMClient),
        (PartialCalcEngine(), CalcEngine),
        (PartialClock(), Clock),
        (PartialEventBus(), EventBus),
        (PartialMediaStore(), MediaStore),
    ],
)
def test_incomplete_stub_fails_isinstance(stub: Any, protocol: type) -> None:
    assert not isinstance(stub, protocol)


@pytest.mark.unit
def test_protocols_are_runtime_checkable() -> None:
    for protocol in ALL_PROTOCOLS:
        assert getattr(protocol, "_is_runtime_protocol", False) is True
