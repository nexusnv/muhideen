"""SqlitePrayerRepo: save/get/last_known behind the port (slice 1A-5, Task 2)."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database, SqlitePrayerRepo
from muhideen.core.ports import PrayerRepo
from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.integration

_ZONE = "SGR01"


_FETCHED_AT = datetime.fromisoformat("2025-10-20T07:00:00+08:00")


def _day(
    day: date,
    zone: str = _ZONE,
    fajr: time = time(5, 45),
    fetched_at: datetime = _FETCHED_AT,
) -> PrayerDay:
    return PrayerDay(
        date=day,
        zone=zone,
        imsak=time(5, 35),
        fajr=fajr,
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=fetched_at,
    )


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    return database


def test_save_get_round_trips_all_eight_markers_and_provenance(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    day = _day(date(2025, 10, 20))
    repo.save_day(day)
    loaded = repo.get_day(date(2025, 10, 20), _ZONE)
    assert loaded == day
    assert loaded is not None
    assert loaded.fetched_at.tzinfo is not None  # tz survives the round-trip
    assert loaded.imsak == time(5, 35)
    assert loaded.dhuha == time(7, 25)


@pytest.mark.parametrize(
    ("requested", "zone"),
    [
        (date(2025, 10, 21), _ZONE),
        (date(2025, 10, 20), "JHR01"),
    ],
)
def test_get_day_miss_returns_none(tmp_path: Path, requested: date, zone: str) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    repo.save_day(_day(date(2025, 10, 20)))
    assert repo.get_day(requested, zone) is None


def test_save_day_upserts_unique_date_zone(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    repo.save_day(_day(date(2025, 10, 20), fajr=time(5, 45)))
    repo.save_day(_day(date(2025, 10, 20), fajr=time(5, 50)))
    with db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM prayer_times").fetchone()[0]
    assert count == 1
    loaded = repo.get_day(date(2025, 10, 20), _ZONE)
    assert loaded is not None
    assert loaded.fajr == time(5, 50)


def test_last_known_returns_nearest_prior_day(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    repo.save_day(_day(date(2025, 10, 18), fajr=time(5, 47)))
    repo.save_day(_day(date(2025, 10, 19), fajr=time(5, 46)))
    repo.save_day(_day(date(2025, 10, 21), fajr=time(5, 44)))
    prior = repo.last_known(date(2025, 10, 20), _ZONE)
    assert prior is not None
    assert prior.date == date(2025, 10, 19)


def test_last_known_is_zone_scoped(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    repo.save_day(_day(date(2025, 10, 19), zone="JHR01"))
    assert repo.last_known(date(2025, 10, 20), _ZONE) is None


def test_last_known_before_any_row_returns_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqlitePrayerRepo(db)
    assert repo.last_known(date(2025, 10, 20), _ZONE) is None


def test_sqlite_prayer_repo_satisfies_port(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert isinstance(SqlitePrayerRepo(db), PrayerRepo)
