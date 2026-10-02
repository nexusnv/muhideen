"""SqliteSettingsRepo: key/value mapping + rules rows (slice 1A-5, Task 3)."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import (
    DISPLAY_SETTINGS_ALLOWLIST,
    Database,
    SqliteDisplaySettingsRepo,
    SqliteSettingsRepo,
)
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


def test_negative_delay_rule_row_raises_config_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "UPDATE iqamah_rules SET delay_minutes = -5 WHERE prayer = 'dhuhr'"
        )
    with pytest.raises(ConfigError, match="delay_minutes"):
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


def test_offset_keys_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    settings = Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        imsak_offset_min=5,
        dhuha_offset_min=20,
    )
    repo.save(settings)
    loaded = repo.load()
    assert (loaded.imsak_offset_min, loaded.dhuha_offset_min) == (5, 20)


def test_offset_keys_default_when_missing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with db.write() as conn:
        conn.execute(
            "DELETE FROM settings WHERE key IN ('imsak_offset_min', 'dhuha_offset_min')"
        )
    loaded = repo.load()
    assert (loaded.imsak_offset_min, loaded.dhuha_offset_min) == (10, 28)


def test_countdown_keys_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    settings = Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        countdown_before_adhan_min=7,
        countdown_before_adhan_overrides={"fajr": 10, "isha": 3},
    )
    repo.save(settings)
    loaded = repo.load()
    assert loaded.countdown_before_adhan_min == 7
    assert loaded.countdown_before_adhan_overrides == {"fajr": 10, "isha": 3}


def test_countdown_keys_default_when_missing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'countdown_min%'")
    loaded = repo.load()
    assert loaded.countdown_before_adhan_min == 5
    assert loaded.countdown_before_adhan_overrides == {}


def test_countdown_seeded_default_present(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    loaded = SqliteSettingsRepo(db).load()
    assert loaded.countdown_before_adhan_min == 5
    assert loaded.countdown_before_adhan_overrides == {}


@pytest.mark.parametrize("value", ["abc", "-1", "91"])
def test_countdown_default_corrupt_raises_config_error(
    tmp_path: Path, value: str
) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("countdown_min_default", value),
        )
    with pytest.raises(ConfigError):
        SqliteSettingsRepo(db).load()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("countdown_min_fajr", "abc"),
        ("countdown_min_fajr", "91"),
        ("countdown_min_bogus", "10"),
        ("countdown_min_imsak", "10"),
    ],
)
def test_countdown_override_corrupt_raises_config_error(
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


def test_save_drops_removed_countdown_overrides(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(
        Settings(
            masjid_name="Masjid Test",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_overrides={"fajr": 10},
        )
    )
    assert repo.load().countdown_before_adhan_overrides == {"fajr": 10}
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    loaded = repo.load()
    assert loaded.countdown_before_adhan_overrides == {}
    assert "countdown_min_fajr" not in _settings_kv(db)


def test_theme_keys_round_trip(tmp_path: Path) -> None:
    from muhideen.core.values import ThemeSettings

    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    settings = Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        theme=ThemeSettings(
            palette="midnight",
            font="system",
            countdown_style="inline",
            clock_format="12h",
            hijri_form="short",
            boundary_strip="hide",
            density="compact",
        ),
    )
    repo.save(settings)
    loaded = repo.load()
    assert loaded.theme == settings.theme
    kv = _settings_kv(db)
    assert kv["theme.palette"] == "midnight"
    assert kv["theme.countdown_style"] == "inline"
    assert kv["theme.clock_format"] == "12h"


def test_theme_keys_default_when_missing(tmp_path: Path) -> None:
    from muhideen.core.values import ThemeSettings

    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'theme.%'")
    assert repo.load().theme == ThemeSettings()


def test_theme_seeded_default_present(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    loaded = SqliteSettingsRepo(db).load()
    assert loaded.theme.palette == "classic-green"
    assert loaded.theme.countdown_style == "boxes"


def test_theme_default_round_trips_without_countdown_clash(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(
        Settings(
            masjid_name="Masjid Test",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_overrides={"fajr": 10},
        )
    )
    kv = _settings_kv(db)
    assert kv["countdown_min_default"] == "5"
    assert kv["theme.palette"] == "classic-green"


@pytest.mark.parametrize("value", ["neon", "", "MIDNIGHT"])
def test_theme_corrupt_raises_config_error(tmp_path: Path, value: str) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("theme.palette", value),
        )
    with pytest.raises(ConfigError):
        SqliteSettingsRepo(db).load()


def test_display_settings_table_exists_after_migrate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.read() as conn:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(display_settings)").fetchall()
        ]
    assert columns == ["display_id", "key", "value"]
    assert (
        frozenset(
            {
                "theme.palette",
                "theme.font",
                "theme.countdown_style",
                "theme.clock_format",
                "theme.hijri_form",
                "theme.boundary_strip",
                "theme.density",
                "dim_minutes_override",
            }
        )
        == DISPLAY_SETTINGS_ALLOWLIST
    )


def test_theme_mappers_share_seam_tables(tmp_path: Path) -> None:
    from muhideen.adapters.sqlite_repo import (
        _theme_from_kv,
        _theme_pairs,
    )
    from muhideen.core.values import (
        THEME_KV_KEYS,
        ThemeSettings,
        theme_from_kv,
        theme_pairs,
    )

    assert _theme_pairs(ThemeSettings()) == theme_pairs(ThemeSettings())
    assert _theme_from_kv({}) == theme_from_kv({})
    assert frozenset([*THEME_KV_KEYS, "dim_minutes_override"]) == (
        DISPLAY_SETTINGS_ALLOWLIST
    )


def test_display_settings_overrides_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
    repo = SqliteDisplaySettingsRepo(db)
    repo.set_override("HALL-01", "theme.palette", "midnight")
    repo.set_override("HALL-01", "dim_minutes_override", "30")
    assert repo.overrides_for("HALL-01") == {
        "theme.palette": "midnight",
        "dim_minutes_override": "30",
    }
    assert repo.overrides_for("UNKNOWN-ID") == {}


def test_display_settings_rejects_keys_outside_allowlist(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteDisplaySettingsRepo(db)
    with pytest.raises(ValueError, match="not an overridable display setting"):
        repo.set_override("HALL-01", "masjid_name", "Other")
    with pytest.raises(ValueError, match="not an overridable display setting"):
        repo.set_override("HALL-01", "theme_default", "x")


@pytest.mark.parametrize("value", ["midnight", "neon", "4", "61", "abc"])
def test_display_settings_validates_values(tmp_path: Path, value: str) -> None:
    db = _db(tmp_path)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
    repo = SqliteDisplaySettingsRepo(db)
    if value == "midnight":
        repo.set_override("HALL-01", "theme.palette", value)
        assert repo.overrides_for("HALL-01")["theme.palette"] == "midnight"
    else:
        key = "theme.palette" if value in ("neon",) else "dim_minutes_override"
        with pytest.raises(ValueError):
            repo.set_override("HALL-01", key, value)


def test_asr_juristic_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    assert repo.load().asr_juristic == "shafi"
    repo.save(
        Settings(
            masjid_name="Masjid Test",
            zone="SGR01",
            hijri_offset=0,
            asr_juristic="hanafi",
        )
    )
    assert repo.load().asr_juristic == "hanafi"


def test_asr_juristic_corrupt_raises_config_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_identity(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("asr_juristic", "maliki"),
        )
    with pytest.raises(ConfigError):
        SqliteSettingsRepo(db).load()


def test_settings_timezone_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(
        Settings(
            masjid_name="Masjid Test",
            zone="SGR01",
            hijri_offset=0,
            timezone="Europe/London",
        )
    )
    assert repo.load().timezone == "Europe/London"
    assert _settings_kv(db)["timezone"] == "Europe/London"


def test_settings_timezone_defaults_for_legacy_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'timezone'")
    assert repo.load().timezone == "Asia/Kuala_Lumpur"


def test_adhan_audio_keys_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    settings = Settings(
        masjid_name="Masjid Test",
        zone="SGR01",
        hijri_offset=0,
        adhan_audio_enabled=True,
        adhan_volume=40,
        quiet_hours_start="22:00",
        quiet_hours_end="06:00",
        adhan_muted_prayers=["fajr"],
    )
    repo.save(settings)
    loaded = repo.load()
    assert loaded == settings
    assert (loaded.adhan_audio_enabled, loaded.adhan_volume) == (True, 40)
    assert (loaded.quiet_hours_start, loaded.quiet_hours_end) == ("22:00", "06:00")
    assert loaded.adhan_muted_prayers == ["fajr"]


def test_adhan_audio_keys_default_for_legacy_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = SqliteSettingsRepo(db)
    repo.save(Settings(masjid_name="Masjid Test", zone="SGR01", hijri_offset=0))
    with db.write() as conn:
        conn.execute(
            "DELETE FROM settings WHERE key IN"
            " ('adhan_audio_enabled', 'adhan_volume', 'quiet_hours_start',"
            " 'quiet_hours_end', 'adhan_muted_prayers')"
        )
    loaded = repo.load()
    assert loaded.adhan_audio_enabled is False
    assert loaded.adhan_volume == 70
    assert loaded.quiet_hours_start is None
    assert loaded.quiet_hours_end is None
    assert loaded.adhan_muted_prayers == []
