"""Migration runner + full PRD §6.2 schema (slice 1A-5, Task 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from muhideen.adapters.migrate import (
    _user_version_stmt,
    current_version,
    migrate,
    migrate_down,
)
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
    "playlists",
    "playlist_items",
    "display_settings",
}

_SEEDED_SETTINGS = {
    "hijri_offset": "0",
    "adhan_duration_s": "180",
    "dim_minutes_default": "20",
    "dim_minutes_jumuah": "45",
    "imsak_offset_min": "10",
    "dhuha_offset_min": "28",
    "countdown_min_default": "5",
    "boundary_countdown": "0",
    "method": "MABIMS",
    "theme.palette": "classic-green",
    "theme.font": "outfit",
    "theme.countdown_style": "boxes",
    "theme.clock_format": "12h",
    "theme.hijri_form": "long",
    "theme.boundary_strip": "show",
    "theme.density": "comfortable",
}

_SEEDED_SETTINGS_V1 = {
    key: value
    for key, value in _SEEDED_SETTINGS.items()
    if not key.startswith("theme.")
}
"""0001-era seed set: down-migrations below 0003 remove the theme keys."""

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
    assert migrate(db) == 4
    assert current_version(db) == 4


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
    assert migrate(db) == 4
    assert current_version(db) == 4
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
    assert migrate(db) == 4
    assert current_version(db) == 4
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
    assert migrate(db) == 4  # re-executes all scripts over present tables
    assert current_version(db) == 4
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


def test_user_version_stmt_rejects_non_builtin_int() -> None:
    # Security guard (Sourcery review): an int subclass can override
    # __format__ and return arbitrary SQL, so only exact built-in ints
    # are admitted before the value reaches the PRAGMA statement.
    class Evil(int):
        def __format__(self, spec: str) -> str:
            return "0; DROP TABLE settings--"

    with pytest.raises(TypeError, match="built-in int"):
        _user_version_stmt(Evil(1))
    with pytest.raises(TypeError, match="built-in int"):
        _user_version_stmt("1")
    assert _user_version_stmt(1) == "PRAGMA user_version = 1"


def test_migrate_0002_creates_playlist_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    with db.read() as conn:
        playlist_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(playlists)").fetchall()
        ]
        item_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(playlist_items)").fetchall()
        ]
        fk_rows = conn.execute("PRAGMA foreign_key_list(playlist_items)").fetchall()
        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    assert playlist_columns == [
        "id",
        "title",
        "active",
        "window_start",
        "window_end",
        "anchor_marker",
        "anchor_start_offset_min",
        "anchor_stop_offset_min",
        "cycle_mode",
        "max_cycles",
    ]
    assert item_columns == [
        "id",
        "playlist_id",
        "image_path",
        "duration_s",
        "sort_order",
    ]
    assert [(row[2], row[3], row[6]) for row in fk_rows] == [
        ("playlists", "playlist_id", "CASCADE")
    ]
    assert "idx_playlist_items_order" in index_names


def test_migrate_down_to_one_drops_only_playlist_tables(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert migrate_down(db, 1) == 1
    assert current_version(db) == 1
    assert _table_names(db) == _TABLES - {
        "playlists",
        "playlist_items",
        "display_settings",
    }
    assert _settings_kv(db) == _SEEDED_SETTINGS_V1
    assert _rule_rows(db) == _FR_1_4_RULES
    assert migrate(db) == 4
    assert _table_names(db) == _TABLES


def test_migrate_0003_creates_display_settings_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    with db.read() as conn:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(display_settings)").fetchall()
        ]
        fk_rows = conn.execute("PRAGMA foreign_key_list(display_settings)").fetchall()
    assert columns == ["display_id", "key", "value"]
    assert [(row[2], row[3], row[6]) for row in fk_rows] == [
        ("displays", "display_id", "CASCADE")
    ]


def test_migrate_down_to_two_drops_only_display_settings(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert migrate_down(db, 2) == 2
    assert current_version(db) == 2
    assert _table_names(db) == _TABLES - {"display_settings"}
    kv = _settings_kv(db)
    assert not [key for key in kv if key.startswith("theme.")]
    assert migrate(db) == 4
    assert _table_names(db) == _TABLES
    assert _settings_kv(db) == _SEEDED_SETTINGS


def test_migrate_0004_flips_seeded_clock_default_only(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert _settings_kv(db)["theme.clock_format"] == "12h"
    # Upgrade path: a row seeded at the old default flips to the new one.
    with db.write() as conn:
        conn.execute(
            "UPDATE settings SET value = '24h-seconds' WHERE key = 'theme.clock_format'"
        )
        conn.execute("PRAGMA user_version = 3")
    assert migrate(db) == 4
    assert _settings_kv(db)["theme.clock_format"] == "12h"
    # An explicit 24h choice is not the seeded default: left untouched.
    with db.write() as conn:
        conn.execute(
            "UPDATE settings SET value = '24h' WHERE key = 'theme.clock_format'"
        )
        conn.execute("PRAGMA user_version = 3")
    assert migrate(db) == 4
    assert _settings_kv(db)["theme.clock_format"] == "24h"


def test_migrate_0004_down_restores_previous_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    assert migrate_down(db, 3) == 3
    assert _settings_kv(db)["theme.clock_format"] == "24h-seconds"
    assert migrate(db) == 4
    assert _settings_kv(db)["theme.clock_format"] == "12h"
