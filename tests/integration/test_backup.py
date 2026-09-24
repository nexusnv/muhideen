"""VACUUM INTO backup primitive (slice 1A-5, Task 5)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, time
from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import (
    Database,
    SqlitePrayerRepo,
    SqliteSettingsRepo,
    backup_to,
)
from muhideen.core.values import PrayerDay, ScheduleSource, Settings

pytestmark = pytest.mark.integration


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    return database


def _day() -> PrayerDay:
    return PrayerDay(
        date=datetime(2025, 10, 20, 7, 0).date(),
        zone="SGR01",
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime.fromisoformat("2025-10-20T07:00:00+08:00"),
    )


def _settings_kv(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row[0]: row[1] for row in rows}


def test_backup_preserves_data_and_user_version(tmp_path: Path) -> None:
    db = _db(tmp_path)
    SqliteSettingsRepo(db).save(
        Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0)
    )
    SqlitePrayerRepo(db).save_day(_day())
    dest = tmp_path / "b.sqlite3"
    assert backup_to(db, dest) == dest
    conn = sqlite3.connect(dest)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "masjid_name" in _settings_kv(conn)
        prayer_count = conn.execute("SELECT COUNT(*) FROM prayer_times").fetchone()
        assert prayer_count is not None and prayer_count[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_backup_to_existing_path_is_rejected(tmp_path: Path) -> None:
    db = _db(tmp_path)
    dest = tmp_path / "b.sqlite3"
    backup_to(db, dest)
    with pytest.raises(sqlite3.OperationalError):
        backup_to(db, dest)  # never silently clobber an existing backup


def test_backup_of_fresh_schema_succeeds(tmp_path: Path) -> None:
    db = _db(tmp_path)
    dest = tmp_path / "fresh.sqlite3"
    backup_to(db, dest)
    conn = sqlite3.connect(dest)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert "settings" in tables and "prayer_times" in tables
        assert len(_settings_kv(conn)) == 8  # migration seeds only (6 + 2 offsets)
    finally:
        conn.close()
