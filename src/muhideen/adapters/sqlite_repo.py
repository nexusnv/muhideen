"""SQLite adapter: connection, single-writer Database, repos, backup.

Implements ADR-0003: stdlib ``sqlite3`` behind the core ports with WAL +
``synchronous=NORMAL`` (PRD §4.2.4). One connection serialised by one lock
is the "single writer" of PRD §7.2 — FastAPI's sync threadpool may call
from many threads at once, so every read, write, migration, and backup
goes through :class:`Database`. Transactions stay short (PRD §5.2).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from muhideen.core.errors import ConfigError
from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    IqamahRule,
    MarkerName,
    PrayerDay,
    ScheduleSource,
    Settings,
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the PRD §4.2.4 pragmas applied."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


class Database:
    """One connection + one lock: every access is serialised."""

    def __init__(self, path: str | Path) -> None:
        self._conn = connect(path)
        self._lock = threading.Lock()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection under the lock (no transaction needed)."""
        with self._lock:
            yield self._conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Short transaction: commit on success, roll back on any error."""
        with self._lock:
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()


def _row_to_day(row: sqlite3.Row) -> PrayerDay:
    """Map one prayer_times row back to the value object (ISO text, tz kept)."""
    return PrayerDay(
        date=date.fromisoformat(row["date_gregorian"]),
        zone=row["zone_code"],
        imsak=time.fromisoformat(row["imsak"]),
        fajr=time.fromisoformat(row["fajr"]),
        syuruq=time.fromisoformat(row["syuruq"]),
        dhuha=time.fromisoformat(row["dhuha"]),
        dhuhr=time.fromisoformat(row["dhuhr"]),
        asr=time.fromisoformat(row["asr"]),
        maghrib=time.fromisoformat(row["maghrib"]),
        isha=time.fromisoformat(row["isha"]),
        source=ScheduleSource(row["source"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
    )


class SqlitePrayerRepo:
    """``PrayerRepo`` over prayer_times: stores rows, validates nothing.

    Ordering and freshness rules belong to 1A-6 (source validation) and
    ``domain/fallback.py`` (staleness) — the repository only round-trips.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT * FROM prayer_times"
                " WHERE date_gregorian = ? AND zone_code = ?",
                (day.isoformat(), zone),
            ).fetchone()
        return _row_to_day(row) if row is not None else None

    def save_day(self, prayer_day: PrayerDay) -> None:
        values = (
            prayer_day.date.isoformat(),
            prayer_day.zone,
            prayer_day.imsak.isoformat(),
            prayer_day.fajr.isoformat(),
            prayer_day.syuruq.isoformat(),
            prayer_day.dhuha.isoformat(),
            prayer_day.dhuhr.isoformat(),
            prayer_day.asr.isoformat(),
            prayer_day.maghrib.isoformat(),
            prayer_day.isha.isoformat(),
            prayer_day.source.value,
            prayer_day.fetched_at.isoformat(),
        )
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO prayer_times ("
                " date_gregorian, zone_code, imsak, fajr, syuruq, dhuha,"
                " dhuhr, asr, maghrib, isha, source, fetched_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(date_gregorian, zone_code) DO UPDATE SET"
                " imsak=excluded.imsak, fajr=excluded.fajr,"
                " syuruq=excluded.syuruq, dhuha=excluded.dhuha,"
                " dhuhr=excluded.dhuhr, asr=excluded.asr,"
                " maghrib=excluded.maghrib, isha=excluded.isha,"
                " source=excluded.source, fetched_at=excluded.fetched_at",
                values,
            )

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        # ISO date text sorts lexicographically == chronologically.
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT * FROM prayer_times"
                " WHERE zone_code = ? AND date_gregorian <= ?"
                " ORDER BY date_gregorian DESC LIMIT 1",
                (zone, day.isoformat()),
            ).fetchone()
        return _row_to_day(row) if row is not None else None


def _parse_bool(raw: str) -> bool:
    """Parse a ``0|1`` settings value; anything else is corruption."""
    if raw == "1":
        return True
    if raw == "0":
        return False
    raise ValueError(f"invalid boolean value: {raw!r}")


def _parse_optional_float(raw: str | None) -> float | None:
    return float(raw) if raw is not None else None


def _rules_from_rows(rows: list[sqlite3.Row]) -> tuple[IqamahRule, ...]:
    rules: list[IqamahRule] = []
    for row in rows:
        fixed = row["fixed_time"]
        rules.append(
            IqamahRule(
                prayer=MarkerName(row["prayer"]),
                mode=row["mode"],
                delay_minutes=row["delay_minutes"],
                fixed_time=(
                    time.fromisoformat(fixed) if fixed is not None else None
                ),
            )
        )
    return tuple(rules)


class SqliteSettingsRepo:
    """``SettingsRepo`` over the settings key/value table + iqamah_rules.

    ``load()`` is the boundary the ``values.py`` docstring reserves for
    this slice: every ``ValueError`` from parsing or from the ``Settings``
    construction guards becomes a ``ConfigError`` here, so callers see one
    typed failure for "the database says something impossible".
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self) -> Settings:
        with self._db.read() as conn:
            kv = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM settings")
            }
            rule_rows = conn.execute(
                "SELECT prayer, mode, delay_minutes, fixed_time"
                " FROM iqamah_rules ORDER BY rowid"
            ).fetchall()
        if "masjid_name" not in kv or "zone_code" not in kv:
            # FR-6.2 first boot: identity comes from the setup wizard.
            raise ConfigError("settings not initialised — run the setup wizard")
        try:
            # Optional keys fall back to VO defaults; migration 0001 seeds
            # the same values, and the seeded-defaults test pins both sides.
            return Settings(
                masjid_name=kv["masjid_name"],
                zone=kv["zone_code"],
                hijri_offset=int(kv["hijri_offset"]),
                adhan_duration_s=int(kv.get("adhan_duration_s", "180")),
                dim_minutes_default=int(kv.get("dim_minutes_default", "20")),
                dim_minutes_jumuah=int(kv.get("dim_minutes_jumuah", "45")),
                method=kv.get("method", "MABIMS"),
                boundary_countdown=_parse_bool(kv.get("boundary_countdown", "0")),
                lat=_parse_optional_float(kv.get("lat")),
                lon=_parse_optional_float(kv.get("lon")),
                iqamah_rules=_rules_from_rows(rule_rows) or DEFAULT_IQAMAH_RULES,
            )
        except (ValueError, KeyError) as exc:
            raise ConfigError(str(exc)) from exc

    def save(self, settings: Settings) -> None:
        pairs: list[tuple[str, str]] = [
            ("masjid_name", settings.masjid_name),
            ("zone_code", settings.zone),
            ("hijri_offset", str(settings.hijri_offset)),
            ("adhan_duration_s", str(settings.adhan_duration_s)),
            ("dim_minutes_default", str(settings.dim_minutes_default)),
            ("dim_minutes_jumuah", str(settings.dim_minutes_jumuah)),
            ("method", settings.method),
            ("boundary_countdown", "1" if settings.boundary_countdown else "0"),
        ]
        if settings.lat is not None:  # both-or-neither, enforced by the VO guard
            pairs.append(("lat", repr(settings.lat)))
            pairs.append(("lon", repr(settings.lon)))
        rule_values = [
            (
                rule.prayer.value,
                rule.mode,
                rule.delay_minutes,
                rule.fixed_time.isoformat()
                if rule.fixed_time is not None
                else None,
            )
            for rule in settings.iqamah_rules
        ]
        # One short transaction: a crash never yields half a settings write.
        with self._db.write() as conn:
            if settings.lat is None:
                conn.execute(
                    "DELETE FROM settings WHERE key IN (?, ?)", ("lat", "lon")
                )
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                pairs,
            )
            conn.execute("DELETE FROM iqamah_rules")
            conn.executemany(
                "INSERT INTO iqamah_rules"
                " (prayer, mode, delay_minutes, fixed_time) VALUES (?, ?, ?, ?)",
                rule_values,
            )
