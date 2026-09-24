"""Seed entrypoint: configure-or-sync branch, then run_sync verbatim (1A-8).

First boot creates `Settings` from `--zone` (required there); a configured
installation always syncs the **configured** zone — a differing `--zone`
or `--masjid-name` is a logged no-op, never an overwrite (decision 3).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from muhideen import seed
from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import (
    Database,
    SqlitePrayerRepo,
    SqliteSettingsRepo,
)
from muhideen.core.errors import ConfigError
from muhideen.core.values import PrayerDay, ScheduleSource, Settings

pytestmark = pytest.mark.integration

KL = timezone(timedelta(hours=8))


class _FakeClock:
    """SystemClock double: pinned now, static monotonic (file-local)."""

    def __init__(self, tz: ZoneInfo) -> None:
        self._tz = tz

    def now(self) -> datetime:
        return datetime(2025, 10, 20, 12, 0, tzinfo=KL)

    def monotonic(self) -> float:
        return 1000.0


def _day(day: date, zone: str) -> PrayerDay:
    return PrayerDay(
        date=day,
        zone=zone,
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 20, 1, 0, tzinfo=KL),
    )


class _FakeClient:
    """JAKIMClient double: records every requested zone, returns 2 days."""

    def __init__(self) -> None:
        self.fetch_zones: list[str] = []
        self.days: list[PrayerDay] = []

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        self.fetch_zones.append(zone)
        self.days = [_day(date(2025, 10, 20), zone), _day(date(2025, 10, 21), zone)]
        return self.days


def _patch_wiring(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    """Route main()'s wiring to the doubles; the real client is never built."""
    monkeypatch.setattr(seed, "HttpJAKIMClient", lambda *, clock: fake)
    monkeypatch.setattr(seed, "SystemClock", _FakeClock)


def test_first_boot_creates_settings_and_syncs_zone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    _patch_wiring(monkeypatch, fake)
    db = tmp_path / "muhideen.db"

    assert seed.main(["--db", str(db), "--zone", "SGR01"]) == 0

    database = Database(db)
    settings = SqliteSettingsRepo(database).load()
    assert settings.zone == "SGR01"
    assert settings.masjid_name == ""
    assert settings.hijri_offset == 0

    assert fake.fetch_zones == ["SGR01"]
    prayer_repo = SqlitePrayerRepo(database)
    assert fake.days, "the fake client must have offered a year"
    for day in fake.days:
        assert prayer_repo.get_day(day.date, day.zone) is not None


def test_unconfigured_without_zone_fails_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _FakeClient()
    _patch_wiring(monkeypatch, fake)
    db = tmp_path / "muhideen.db"

    assert seed.main(["--db", str(db)]) != 0

    assert "--zone" in capsys.readouterr().err
    assert fake.fetch_zones == []  # sync never ran
    with pytest.raises(ConfigError):
        SqliteSettingsRepo(Database(db)).load()  # still unconfigured


def test_configured_db_syncs_configured_zone_and_never_overwrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "muhideen.db"
    database = Database(db)
    migrate(database)
    SqliteSettingsRepo(database).save(
        Settings(
            masjid_name="Masjid Original",
            zone="SGR01",
            hijri_offset=2,
            lat=3.07,
            lon=101.69,
        )
    )
    fake = _FakeClient()
    _patch_wiring(monkeypatch, fake)

    # Mismatched --zone and a rename request: both are logged no-ops.
    assert (
        seed.main(["--db", str(db), "--zone", "JHR01", "--masjid-name", "Renamed"]) == 0
    )
    notice = capsys.readouterr().err
    assert "notice" in notice and "JHR01" in notice and "SGR01" in notice

    # A matching --zone syncs quietly (no notice, configured zone fetched).
    assert seed.main(["--db", str(db), "--zone", "SGR01"]) == 0
    assert capsys.readouterr().err == ""

    assert fake.fetch_zones == ["SGR01", "SGR01"]
    settings = SqliteSettingsRepo(Database(db)).load()
    assert settings.zone == "SGR01"
    assert settings.masjid_name == "Masjid Original"
    assert settings.hijri_offset == 2
