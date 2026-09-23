"""SqliteSettingsRepo: key/value mapping + rules rows (slice 1A-5, Task 3)."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database, SqliteSettingsRepo
from muhideen.core.errors import ConfigError
from muhideen.core.ports import SettingsRepo
from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    IqamahRule,
    MarkerName,
    Settings,
)

pytestmark = pytest.mark.integration


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    return database


def _seed_identity(db: Database) -> None:
    """Insert only the identity rows a first-boot wizard would write."""
    with db.write() as conn:
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            [("masjid_name", "Masjid Test"), ("zone_code", "SGR01")],
        )


def _settings_kv(db: Database) -> dict[str, str]:
    with db.read() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


_CUSTOM_RULES = (
    IqamahRule(prayer=MarkerName.FAJR, mode="fixed", fixed_time=time(5, 55)),
    IqamahRule(prayer=MarkerName.DHUHR, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.ASR, mode="delay", delay_minutes=10),
    IqamahRule(prayer=MarkerName.MAGHRIB, mode="delay", delay_minutes=12),
    IqamahRule(prayer=MarkerName.ISHA, mode="delay", delay_minutes=15),
    IqamahRule(prayer=MarkerName.JUMUAH, mode="delay", delay_minutes=20),
)


def test_load_before_setup_raises_config_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with pytest.raises(ConfigError, match="setup"):
        SqliteSettingsRepo(db).load()


def test_load_returns_seeded_defaults_given_identity(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    loaded = SqliteSettingsRepo(db).load()
    assert loaded == Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0)
    assert loaded.boundary_countdown is False
    assert loaded.method == "MABIMS"
    assert loaded.lat is None and loaded.lon is None
    assert loaded.iqamah_rules == DEFAULT_IQAMAH_RULES


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    settings = Settings(
        masjid_name="Masjid Sri Gombak",
        zone="SGR01",
        hijri_offset=-1,
        adhan_duration_s=300,
        dim_minutes_default=25,
        dim_minutes_jumuah=50,
        boundary_countdown=True,
        lat=3.1,
        lon=101.6,
        method="MWL",
        iqamah_rules=_CUSTOM_RULES,
    )
    repo.save(settings)
    assert repo.load() == settings


def test_save_with_unset_coordinates_deletes_those_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    with_coords = Settings(
        masjid_name="Masjid Test", zone="SGR01", hijri_offset=0, lat=3.1, lon=101.6
    )
    repo.save(with_coords)
    assert repo.load().lat == 3.1
    without_coords = Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0)
    repo.save(without_coords)
    loaded = repo.load()
    assert loaded.lat is None and loaded.lon is None
    assert "lat" not in _settings_kv(db) and "lon" not in _settings_kv(db)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("hijri_offset", "abc"),
        ("boundary_countdown", "2"),
        ("lat", "not-a-float"),
    ],
)
def test_load_malformed_value_raises_config_error(
    tmp_path: Path, key: str, value: str
) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    with pytest.raises(ConfigError):
        SqliteSettingsRepo(db).load()


def test_load_out_of_range_hijri_offset_raises_config_error(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute("UPDATE settings SET value = '5' WHERE key = 'hijri_offset'")
    with pytest.raises(ConfigError, match="hijri_offset"):
        SqliteSettingsRepo(db).load()


def test_boundary_iqamah_rule_row_raises_config_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO iqamah_rules (prayer, mode, delay_minutes) VALUES"
            " ('imsak', 'delay', 10)"
        )
    with pytest.raises(ConfigError, match="imsak"):
        SqliteSettingsRepo(db).load()


def test_emptied_rules_table_falls_back_to_defaults(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute("DELETE FROM iqamah_rules")
    loaded = SqliteSettingsRepo(db).load()
    assert loaded.iqamah_rules == DEFAULT_IQAMAH_RULES


def test_save_persists_rules_as_canonical_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(
        Settings(
            masjid_name="Masjid Test",
            zone="SGR01",
            hijri_offset=0,
            iqamah_rules=_CUSTOM_RULES,
        )
    )
    with db.read() as conn:
        rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT prayer, mode, delay_minutes, fixed_time"
                " FROM iqamah_rules ORDER BY rowid"
            ).fetchall()
        ]
    expected = [
        (
            rule.prayer.value,
            rule.mode,
            rule.delay_minutes,
            rule.fixed_time.isoformat() if rule.fixed_time is not None else None,
        )
        for rule in _CUSTOM_RULES
    ]
    assert rows == expected


def test_sqlite_settings_repo_satisfies_port(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert isinstance(SqliteSettingsRepo(db), SettingsRepo)
