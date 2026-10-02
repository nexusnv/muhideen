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
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import cast

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError

from muhideen.core.errors import ConfigError, SettingsNotInitializedError
from muhideen.core.ports import Clock
from muhideen.core.values import (
    DEFAULT_IQAMAH_RULES,
    THEME_KV_KEYS,
    TIMEZONE_DEFAULT,
    AsrJuristic,
    IqamahRule,
    MarkerName,
    PrayerDay,
    ScheduleSource,
    Settings,
    ThemeSettings,
    theme_choices,
    theme_from_kv,
    theme_pairs,
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
        """Open the connection and create the serialising lock."""
        self._conn = connect(path)
        self._lock = threading.Lock()

    @contextmanager
    def read(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield the connection under the lock (no transaction needed)."""
        with self._lock:
            yield self._conn

    @contextmanager
    def write(self) -> Generator[sqlite3.Connection, None, None]:
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
        """Hold the shared single-writer database handle."""
        self._db = db

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        """Return the stored day for date+zone, else None."""
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT * FROM prayer_times WHERE date_gregorian = ? AND zone_code = ?",
                (day.isoformat(), zone),
            ).fetchone()
        return _row_to_day(row) if row is not None else None

    def save_day(self, prayer_day: PrayerDay) -> None:
        """Upsert one day keyed by (date_gregorian, zone_code)."""
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
        """Return the newest saved day on or before ``day``, else None."""
        # ISO date text sorts lexicographically == chronologically.
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT * FROM prayer_times"
                " WHERE zone_code = ? AND date_gregorian <= ?"
                " ORDER BY date_gregorian DESC LIMIT 1",
                (zone, day.isoformat()),
            ).fetchone()
        return _row_to_day(row) if row is not None else None

    def delete_day(self, day: date, zone: str) -> None:
        """Delete the saved day for date+zone (manual-pin release)."""
        with self._db.write() as conn:
            conn.execute(
                "DELETE FROM prayer_times WHERE date_gregorian = ? AND zone_code = ?",
                (day.isoformat(), zone),
            )


def _parse_bool(raw: str) -> bool:
    """Parse a ``0|1`` settings value; anything else is corruption."""
    if raw == "1":
        return True
    if raw == "0":
        return False
    raise ValueError(f"invalid boolean value: {raw!r}")


def _parse_optional_float(raw: str | None) -> float | None:
    """Parse an optional coordinate string; None stays None."""
    return float(raw) if raw is not None else None


DISPLAY_SETTINGS_ALLOWLIST: frozenset[str] = frozenset(
    (*THEME_KV_KEYS, "dim_minutes_override")
)
"""Per-display override keys: the seven theme knobs plus the dim pin.

Anything else (identity, schedule, iqamah) stays global — a display
override must never fork the schedule, only its presentation.
"""


def _theme_from_kv(kv: dict[str, str]) -> ThemeSettings:
    """Build ThemeSettings from settings rows; missing keys take defaults.

    Unknown stored values raise ``ValueError`` (mapped to ``ConfigError``
    by the caller), matching the countdown-key corruption handling.
    """
    return theme_from_kv(kv)


def _theme_pairs(theme: ThemeSettings) -> list[tuple[str, str]]:
    """Render one ThemeSettings as its seven ``theme.*`` settings rows."""
    return theme_pairs(theme)


def _rules_from_rows(rows: list[sqlite3.Row]) -> tuple[IqamahRule, ...]:
    """Map iqamah_rows to domain rules in stored order."""
    rules: list[IqamahRule] = []
    for row in rows:
        fixed = row["fixed_time"]
        rules.append(
            IqamahRule(
                prayer=MarkerName(row["prayer"]),
                mode=row["mode"],
                delay_minutes=row["delay_minutes"],
                fixed_time=(time.fromisoformat(fixed) if fixed is not None else None),
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
        """Hold the shared database for the settings tables."""
        self._db = db

    def load(self) -> Settings:
        """Load settings; raise ConfigError on missing or corrupt rows."""
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
            raise SettingsNotInitializedError(
                "settings not initialised — run the setup wizard"
            )
        try:
            # Optional keys fall back to VO defaults; migration 0001 seeds
            # the same values, and the seeded-defaults test pins both sides.
            return Settings(
                masjid_name=kv["masjid_name"],
                zone=kv["zone_code"],
                hijri_offset=int(kv["hijri_offset"]),
                timezone=kv.get("timezone", TIMEZONE_DEFAULT),
                adhan_duration_s=int(kv.get("adhan_duration_s", "180")),
                dim_minutes_default=int(kv.get("dim_minutes_default", "20")),
                dim_minutes_jumuah=int(kv.get("dim_minutes_jumuah", "45")),
                method=kv.get("method", "MABIMS"),
                asr_juristic=cast(AsrJuristic, kv.get("asr_juristic", "shafi")),
                boundary_countdown=_parse_bool(kv.get("boundary_countdown", "0")),
                calc_only=_parse_bool(kv.get("calc_only", "0")),
                imsak_offset_min=int(kv.get("imsak_offset_min", "10")),
                dhuha_offset_min=int(kv.get("dhuha_offset_min", "28")),
                countdown_before_adhan_min=int(kv.get("countdown_min_default", "5")),
                countdown_before_adhan_overrides={
                    key.removeprefix("countdown_min_"): int(value)
                    for key, value in kv.items()
                    if key.startswith("countdown_min_")
                    and key != "countdown_min_default"
                },
                adhan_audio_enabled=_parse_bool(kv.get("adhan_audio_enabled", "0")),
                adhan_volume=int(kv.get("adhan_volume", "70")),
                quiet_hours_start=kv.get("quiet_hours_start"),
                quiet_hours_end=kv.get("quiet_hours_end"),
                adhan_muted_prayers=sorted(
                    part
                    for part in kv.get("adhan_muted_prayers", "").split(",")
                    if part
                ),
                lat=_parse_optional_float(kv.get("lat")),
                lon=_parse_optional_float(kv.get("lon")),
                iqamah_rules=_rules_from_rows(rule_rows) or DEFAULT_IQAMAH_RULES,
                theme=_theme_from_kv(kv),
            )
        except (ValueError, KeyError) as exc:
            raise ConfigError(str(exc)) from exc

    def save(self, settings: Settings) -> None:
        """Write settings keys plus the full iqamah rule set atomically."""
        pairs: list[tuple[str, str]] = [
            ("masjid_name", settings.masjid_name),
            ("zone_code", settings.zone),
            ("timezone", settings.timezone),
            ("hijri_offset", str(settings.hijri_offset)),
            ("adhan_duration_s", str(settings.adhan_duration_s)),
            ("dim_minutes_default", str(settings.dim_minutes_default)),
            ("dim_minutes_jumuah", str(settings.dim_minutes_jumuah)),
            ("method", settings.method),
            ("asr_juristic", settings.asr_juristic),
            ("imsak_offset_min", str(settings.imsak_offset_min)),
            ("dhuha_offset_min", str(settings.dhuha_offset_min)),
            (
                "countdown_min_default",
                str(settings.countdown_before_adhan_min),
            ),
            *[
                (f"countdown_min_{prayer}", str(minutes))
                for prayer, minutes in sorted(
                    settings.countdown_before_adhan_overrides.items()
                )
            ],
            *_theme_pairs(settings.theme),
            ("boundary_countdown", "1" if settings.boundary_countdown else "0"),
            ("calc_only", "1" if settings.calc_only else "0"),
            (
                "adhan_audio_enabled",
                "1" if settings.adhan_audio_enabled else "0",
            ),
            ("adhan_volume", str(settings.adhan_volume)),
            (
                "adhan_muted_prayers",
                ",".join(sorted(settings.adhan_muted_prayers)),
            ),
        ]
        if (
            settings.quiet_hours_start is not None
            and settings.quiet_hours_end is not None
        ):  # both-or-neither, enforced by the VO guard
            pairs.append(("quiet_hours_start", settings.quiet_hours_start))
            pairs.append(("quiet_hours_end", settings.quiet_hours_end))
        if settings.lat is not None:  # both-or-neither, enforced by the VO guard
            pairs.append(("lat", repr(settings.lat)))
            pairs.append(("lon", repr(settings.lon)))
        rule_values = [
            (
                rule.prayer.value,
                rule.mode,
                rule.delay_minutes,
                rule.fixed_time.isoformat() if rule.fixed_time is not None else None,
            )
            for rule in settings.iqamah_rules
        ]
        # One short transaction: a crash never yields half a settings write.
        with self._db.write() as conn:
            if settings.lat is None:
                conn.execute("DELETE FROM settings WHERE key IN (?, ?)", ("lat", "lon"))
            if settings.quiet_hours_start is None:
                conn.execute(
                    "DELETE FROM settings WHERE key IN (?, ?)",
                    ("quiet_hours_start", "quiet_hours_end"),
                )
            # Overrides are keyed per prayer: clear the namespace first so a
            # dropped override cannot linger and resurrect on the next load.
            conn.execute("DELETE FROM settings WHERE key LIKE 'countdown_min%'")
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


class SqliteDisplaySettingsRepo:
    """Per-display presentation overrides over ``display_settings``.

    Reads return only allowlisted rows (``DISPLAY_SETTINGS_ALLOWLIST``);
    writes validate the key and the value up front — unknown theme values
    and out-of-range dim minutes raise ``ValueError`` before touching the
    database. Unknown display ids read back as ``{}``; writes to them fail
    on the ``displays`` foreign key.
    """

    def __init__(self, db: Database) -> None:
        """Hold the shared database for the display_settings table."""
        self._db = db

    def overrides_for(self, display_id: str) -> dict[str, str]:
        """Return the allowlisted override rows for one display id."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT key, value FROM display_settings WHERE display_id = ?",
                (display_id,),
            ).fetchall()
        return {
            row["key"]: row["value"]
            for row in rows
            if row["key"] in DISPLAY_SETTINGS_ALLOWLIST
        }

    def group_dim_override(self, display_id: str) -> str | None:
        """Group dim pin for one display id, else ``None``.

        Raw stored string (unparsed — the domain Dim module owns parsing
        and the range rule, so a corrupt group row slates exactly like a
        corrupt display row). Unknown ids read back as ``None``.
        """
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT g.dim_minutes_override AS dim FROM displays d"
                " LEFT JOIN display_groups g ON g.name = d.group_name"
                " WHERE d.id = ?",
                (display_id,),
            ).fetchone()
        if row is None or row["dim"] is None:
            return None
        return str(row["dim"])

    def set_override(self, display_id: str, key: str, value: str) -> None:
        """Store one override after allowlist + value validation."""
        if key not in DISPLAY_SETTINGS_ALLOWLIST:
            raise ValueError(f"not an overridable display setting: {key!r}")
        if key.startswith("theme."):
            self._check_theme_value(key, value)
        else:
            self._check_dim_value(key, value)
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO display_settings (display_id, key, value)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(display_id, key) DO UPDATE SET value = excluded.value",
                (display_id, key, value),
            )

    @staticmethod
    def _check_theme_value(key: str, value: str) -> None:
        """Validate one theme knob value through the closed-enum guards."""
        suffix = key.removeprefix("theme.")
        try:
            choices = theme_choices(suffix)
        except KeyError as exc:
            raise ValueError(f"invalid display theme override {key}={value!r}") from exc
        if value not in choices:
            raise ValueError(f"invalid display theme override {key}={value!r}")
        try:
            ThemeSettings(**{suffix: value})  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid display theme override {key}={value!r}") from exc

    @staticmethod
    def _check_dim_value(key: str, value: str) -> None:
        """Validate the per-display dim pin (5–60, mirroring groups)."""
        try:
            minutes = int(value)
        except ValueError:
            raise ValueError(
                f"invalid display dim override {key}={value!r}: not an integer"
            ) from None
        if not 5 <= minutes <= 60:
            raise ValueError(
                f"invalid display dim override {key}={value!r}: out of range 5-60"
            )


class SqliteDisplayRepo:
    """``DisplayRepo``: buffered heartbeats flushed in 60s batches.

    The batch window is driven by the injected ``Clock.monotonic()`` — no
    timer thread in this slice: the HTTP layer (1A-7) calls ``record_seen``
    per heartbeat, and ``flush()`` on shutdown. Time reads never touch the
    wall clock (``TESTING_STRATEGY.md:17``). Only pre-registered display
    IDs are updated; unknown IDs buffer normally but match no row at flush
    and are dropped (``docs/api-contract.md:60``).
    """

    def __init__(
        self,
        db: Database,
        clock: Clock,
        batch_interval_s: float = 60,
    ) -> None:
        """Hold the database, clock, batch window, and heartbeat buffer."""
        self._db = db
        self._clock = clock
        self._batch_interval_s = batch_interval_s
        self._buffer: list[tuple[str, str | None, datetime]] = []
        self._last_flush: float = clock.monotonic()

    def record_seen(self, display_id: str, ip: str | None) -> None:
        """Buffer one heartbeat; flush when the batch window has elapsed."""
        with self._db.write() as conn:
            self._buffer.append((display_id, ip, self._clock.now()))
            if self._clock.monotonic() - self._last_flush >= self._batch_interval_s:
                self._flush_locked(conn)

    def is_registered(self, display_id: str) -> bool:
        """Return True when display_id names a pre-registered display."""
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT 1 FROM displays WHERE id = ?", (display_id,)
            ).fetchone()
        return row is not None

    def flush(self) -> int:
        """Write every buffered heartbeat; return rows updated."""
        with self._db.write() as conn:
            return self._flush_locked(conn)

    def _flush_locked(self, conn: sqlite3.Connection) -> int:
        """Apply the buffer in one transaction; caller holds the lock."""
        # Buffer and window advance only after the writes succeed: a failed
        # flush (I/O error on the Pi) rolls back the DB, keeps the rows, and
        # retries on the next heartbeat instead of dropping a batch and
        # re-arming the full 60s window.
        rows = self._buffer
        count = 0
        if rows:
            cursor = conn.executemany(
                "UPDATE displays SET last_seen = ?, ip_address = ? WHERE id = ?",
                [(stamp.isoformat(), ip, display_id) for display_id, ip, stamp in rows],
            )
            count = cursor.rowcount
            self._buffer = []
        self._last_flush = self._clock.monotonic()
        return count


def backup_to(db: Database, dest: Path) -> Path:
    """Atomic hot backup via ``VACUUM INTO`` (ADR-0003, PRD §4.2.4).

    Fails (``sqlite3.OperationalError``) if ``dest`` already exists —
    backups are never silently clobbered.
    """
    with db.write() as conn:
        conn.execute("VACUUM INTO ?", (str(dest),))
    return dest


class SqliteUserRepo:
    """``UserRepo`` over the ``users`` table with Argon2id hashes."""

    def __init__(self, db: Database) -> None:
        """Hold the database and prepare the Argon2id hasher."""
        self._db = db
        self._hasher = PasswordHasher()

    def has_users(self) -> bool:
        """Return True when at least one admin row exists."""
        with self._db.read() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) > 0

    def create_user(self, username: str, password: str) -> bool:
        """Store an Argon2id hash; False on duplicate username."""
        digest = self._hasher.hash(password)
        try:
            with self._db.write() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, digest),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def verify(self, username: str, password: str) -> bool:
        """Verify a password; False for unknown users or bad hashes."""
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return False
        try:
            return bool(self._hasher.verify(str(row["password_hash"]), password))
        except (VerificationError, InvalidHash):
            return False
