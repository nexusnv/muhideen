"""Migration runner + full PRD §6.2 schema (slice 1A-5, Task 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from muhideen.adapters.migrate import current_version, migrate, migrate_down
from muhideen.adapters.sqlite_repo import Database, connect
from muhideen.core.errors import MuhideenError

pytestmark = pytest.mark.integration

_TABLES = {
    "settings",
    "prayer_times",
    "iqamah_rules",
    "media",
    "display_groups",
    "displays",
    "users",
}

_SEEDED_SETTINGS = {
    "hijri_offset": "0",
    "adhan_duration_s": "180",
    "dim_minutes_default": "20",
    "dim_minutes_jumuah": "45",
    "boundary_countdown": "0",
    "method": "MABIMS",
}

_FR_1_4_RULES = [
    ("fajr", "delay", 15, None),
    ("dhuhr", "delay", 10, None),
    ("asr", "delay", 10, None),
    ("maghrib", "delay", 10, None),
    ("isha", "delay", 15, None),
    ("jumuah", "delay", 10, None),
]


def _table_names(db: Database) -> set[str]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


def _settings_kv(db: Database) -> dict[str, str]:
    with db.read() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _rule_rows(db: Database) -> list[tuple[object, ...]]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT prayer, mode, delay_minutes, fixed_time"
            " FROM iqamah_rules ORDER BY rowid"
        ).fetchall()
    return [tuple(row) for row in rows]


def test_connect_applies_pragmas(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_migrate_sets_user_version_head(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    assert migrate(db) == 1
    assert current_version(db) == 1


def test_migrate_creates_all_prd_tables_and_index(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert _table_names(db) == _TABLES
    with db.read() as conn:
        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(prayer_times)").fetchall()
        ]
        unique_indexes = [
            row
            for row in conn.execute("PRAGMA index_list(prayer_times)").fetchall()
            if row[3] == "u"  # origin: UNIQUE constraint
        ]
    assert "idx_prayer_zone_date" in index_names
    assert columns == [
        "id",
        "date_gregorian",
        "zone_code",
        "imsak",
        "fajr",
        "syuruq",
        "dhuha",
        "dhuhr",
        "asr",
        "maghrib",
        "isha",
        "source",
        "fetched_at",
    ]
    assert unique_indexes, "UNIQUE(date_gregorian, zone_code) missing"


def test_migrate_seeds_non_identity_settings_only(tmp_path: Path) -> None:
    # Exact-dict equality: seeded defaults present, identity keys
    # (masjid_name/zone_code/lat/lon) deliberately absent — FR-6.2 wizard.
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert _settings_kv(db) == _SEEDED_SETTINGS


def test_migrate_seeds_fr_1_4_iqamah_rules(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert _rule_rows(db) == _FR_1_4_RULES


def test_migrate_seeds_default_display_group(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    with db.read() as conn:
        rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT name, theme, carousel_enabled, dim_minutes_override"
                " FROM display_groups"
            ).fetchall()
        ]
    assert rows == [("Default", "classic-green", 1, None)]


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    kv_before = _settings_kv(db)
    rules_before = _rule_rows(db)
    assert migrate(db) == 1
    assert current_version(db) == 1
    assert _settings_kv(db) == kv_before == _SEEDED_SETTINGS
    assert _rule_rows(db) == rules_before == _FR_1_4_RULES


def test_migrate_down_to_zero_drops_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert migrate_down(db) == 0
    assert current_version(db) == 0
    assert _table_names(db) == set()


def test_down_then_up_round_trip_restores_seeds(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    migrate_down(db)
    assert migrate(db) == 1
    assert current_version(db) == 1
    assert _table_names(db) == _TABLES
    assert _settings_kv(db) == _SEEDED_SETTINGS
    assert _rule_rows(db) == _FR_1_4_RULES


def test_migrate_re_runs_script_when_version_bump_was_lost(tmp_path: Path) -> None:
    # Crash window: the script committed but PRAGMA user_version never
    # persisted (migrate.py:7). Re-running against the live schema must
    # not raise and must not duplicate seeded rows (ADR-0003:18).
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    with db.write() as conn:
        conn.execute("PRAGMA user_version = 0")  # simulate the lost bump
    assert migrate(db) == 1  # re-executes 0001 over already-present tables
    assert current_version(db) == 1
    assert _table_names(db) == _TABLES
    assert _settings_kv(db) == _SEEDED_SETTINGS
    assert _rule_rows(db) == _FR_1_4_RULES


def test_migrate_down_without_matching_down_file_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    with db.write() as conn:
        conn.execute("PRAGMA user_version = 42")  # corrupt/future version
    with pytest.raises(MuhideenError, match="no down migration"):
        migrate_down(db)
