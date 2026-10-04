"""File-backed repos over the core ports (no database).

``load_config_file`` parses ``config/muhideen.json`` into the validated
:class:`ConfigFile` model; :class:`FileSettingsRepo` maps it to the
:class:`Settings` domain object, :class:`FilePrayerRepo` layers the config
``manual_days`` pins over a JSON buffer cache, and :class:`FilePlaylistRepo`
maps the playlist section. Every ``ValueError`` from domain construction
becomes :class:`ConfigError` at this boundary, mirroring the former
database-backed repos (read fresh on every call, validated on load).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from muhideen.adapters.file_models import ConfigFile, ManualDay, PlaylistFile
from muhideen.core.errors import ConfigError
from muhideen.core.values import (
    IqamahRule,
    MarkerName,
    Playlist,
    PlaylistItem,
    PrayerDay,
    ScheduleSource,
    Settings,
    ThemeSettings,
)
from muhideen.domain.ordering import ensure_ordered
from muhideen.domain.playlist_window import parse_window

BUFFER_SCHEMA_VERSION = 1
"""Schema tag for the prayer buffer file (bumped on format changes)."""


def load_config_file(path: str | Path) -> ConfigFile:
    """Parse and validate the JSON config file; ``ConfigError`` on failure.

    The error message carries the path plus the underlying pydantic (or
    JSON-syntax) detail so a hand-edit typo points at the file and the
    offending key.
    """
    raw_path = Path(path)
    try:
        text = raw_path.read_text()
    except OSError as exc:
        raise ConfigError(f"{raw_path}: cannot read config file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{raw_path}: invalid JSON: {exc}") from exc
    try:
        return ConfigFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{raw_path}: {exc}") from exc


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON via tmp+rename so a crash never leaves half a file."""
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def _iqamah_rules_from_file(cfg: ConfigFile) -> tuple[IqamahRule, ...]:
    """Map the six file iqamah rules; ``HH:MM`` fixed times become ``time``."""
    rules: list[IqamahRule] = []
    for entry in cfg.timing.iqamah_rules:
        rules.append(
            IqamahRule(
                prayer=MarkerName(entry.prayer),
                mode=entry.mode,
                delay_minutes=entry.delay_minutes,
                fixed_time=(
                    time.fromisoformat(entry.fixed_time)
                    if entry.fixed_time is not None
                    else None
                ),
            )
        )
    return tuple(rules)


def _settings_from_config(cfg: ConfigFile) -> Settings:
    """Map a validated config to the domain object (``ValueError`` -> caller)."""
    audio = cfg.adhan_audio
    return Settings(
        masjid_name=cfg.masjid.name,
        zone=cfg.masjid.zone,
        timezone=cfg.masjid.timezone,
        hijri_offset=cfg.schedule.hijri_offset,
        method=cfg.schedule.method,
        asr_juristic=cfg.schedule.asr_juristic,
        calc_only=cfg.schedule.calc_only,
        boundary_countdown=cfg.schedule.boundary_countdown,
        imsak_offset_min=cfg.schedule.imsak_offset_min,
        dhuha_offset_min=cfg.schedule.dhuha_offset_min,
        lat=cfg.schedule.lat,
        lon=cfg.schedule.lon,
        adhan_duration_s=cfg.timing.adhan_duration_s,
        dim_minutes_default=cfg.timing.dim_minutes_default,
        dim_minutes_jumuah=cfg.timing.dim_minutes_jumuah,
        countdown_before_adhan_min=cfg.timing.countdown_before_adhan_min,
        countdown_before_adhan_overrides=dict(
            cfg.timing.countdown_before_adhan_overrides
        ),
        iqamah_rules=_iqamah_rules_from_file(cfg),
        adhan_audio_enabled=audio.enabled,
        adhan_volume=audio.volume,
        quiet_hours_start=audio.quiet_hours_start,
        quiet_hours_end=audio.quiet_hours_end,
        adhan_muted_prayers=list(audio.muted_prayers),
        theme=ThemeSettings(
            palette=cfg.theme.palette,
            font=cfg.theme.font,
            countdown_style=cfg.theme.countdown_style,
            clock_format=cfg.theme.clock_format,
            hijri_form=cfg.theme.hijri_form,
            boundary_strip=cfg.theme.boundary_strip,
            density=cfg.theme.density,
        ),
    )


class FileSettingsRepo:
    """``SettingsRepo`` over the JSON config file.

    ``save`` round-trips through the current file content: only the mapped
    scalar sections are replaced, so hand-edited ``displays``, ``playlists``,
    ``manual_days``, and the adhan audio ``file`` survive untouched.
    """

    def __init__(self, path: str | Path) -> None:
        """Hold the config file path (reads are fresh on every call)."""
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Config file path (lets the display route load per-display entries)."""
        return self._path

    def load(self) -> Settings:
        """Load settings; ``ConfigError`` on invalid file or domain values."""
        cfg = load_config_file(self._path)
        try:
            return _settings_from_config(cfg)
        except ValueError as exc:
            raise ConfigError(f"{self._path}: {exc}") from exc

    def save(self, settings: Settings) -> None:
        """Persist settings as one atomic full-file replacement."""
        try:
            data = json.loads(self._path.read_text())
        except OSError as exc:
            raise ConfigError(f"{self._path}: cannot read config file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{self._path}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{self._path}: config root must be an object")
        # Raw-dict surgery: only the mapped scalar sections are replaced, so
        # hand-edited displays/playlists/manual_days (and the adhan audio
        # file) survive byte-for-byte.
        data["masjid"] = {
            "name": settings.masjid_name,
            "zone": settings.zone,
            "timezone": settings.timezone,
        }
        schedule = data.get("schedule")
        audio = data.get("adhan_audio")
        if not isinstance(schedule, dict) or not isinstance(audio, dict):
            raise ConfigError(f"{self._path}: config missing schedule/audio sections")
        schedule.update(
            {
                "method": settings.method,
                "asr_juristic": settings.asr_juristic,
                "lat": settings.lat,
                "lon": settings.lon,
                "calc_only": settings.calc_only,
                "hijri_offset": settings.hijri_offset,
                "imsak_offset_min": settings.imsak_offset_min,
                "dhuha_offset_min": settings.dhuha_offset_min,
                "boundary_countdown": settings.boundary_countdown,
            }
        )
        data["timing"] = {
            "adhan_duration_s": settings.adhan_duration_s,
            "dim_minutes_default": settings.dim_minutes_default,
            "dim_minutes_jumuah": settings.dim_minutes_jumuah,
            "countdown_before_adhan_min": settings.countdown_before_adhan_min,
            "countdown_before_adhan_overrides": dict(
                settings.countdown_before_adhan_overrides
            ),
            "iqamah_rules": [
                {
                    "prayer": rule.prayer.value,
                    "mode": rule.mode,
                    "delay_minutes": rule.delay_minutes,
                    "fixed_time": (
                        rule.fixed_time.strftime("%H:%M")
                        if rule.fixed_time is not None
                        else None
                    ),
                }
                for rule in settings.iqamah_rules
            ],
        }
        audio.update(
            {
                "enabled": settings.adhan_audio_enabled,
                "volume": settings.adhan_volume,
                "quiet_hours_start": settings.quiet_hours_start,
                "quiet_hours_end": settings.quiet_hours_end,
                "muted_prayers": list(settings.adhan_muted_prayers),
            }
        )
        data["theme"] = {
            "palette": settings.theme.palette,
            "font": settings.theme.font,
            "countdown_style": settings.theme.countdown_style,
            "clock_format": settings.theme.clock_format,
            "hijri_form": settings.theme.hijri_form,
            "boundary_strip": settings.theme.boundary_strip,
            "density": settings.theme.density,
        }
        try:
            ConfigFile.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"{self._path}: {exc}") from exc
        _atomic_write_json(self._path, data)


_DAY_KEYS: tuple[str, ...] = (
    "imsak",
    "fajr",
    "syuruq",
    "dhuha",
    "dhuhr",
    "asr",
    "maghrib",
    "isha",
)


def _manual_to_day(pin: ManualDay, zone: str) -> PrayerDay:
    """Stamp a config pin with the requested zone; ``SyncError`` when unordered."""
    day = PrayerDay(
        date=pin.date,
        zone=zone,
        imsak=time.fromisoformat(pin.imsak),
        fajr=time.fromisoformat(pin.fajr),
        syuruq=time.fromisoformat(pin.syuruq),
        dhuha=time.fromisoformat(pin.dhuha),
        dhuhr=time.fromisoformat(pin.dhuhr),
        asr=time.fromisoformat(pin.asr),
        maghrib=time.fromisoformat(pin.maghrib),
        isha=time.fromisoformat(pin.isha),
        source=ScheduleSource.MANUAL,
        fetched_at=datetime.now(tz=UTC),
    )
    return ensure_ordered(day)


def _day_to_entry(day: PrayerDay) -> dict[str, str]:
    """Render one PrayerDay as its buffer entry (times + provenance)."""
    return {
        "zone": day.zone,
        "imsak": day.imsak.isoformat(timespec="minutes"),
        "fajr": day.fajr.isoformat(timespec="minutes"),
        "syuruq": day.syuruq.isoformat(timespec="minutes"),
        "dhuha": day.dhuha.isoformat(timespec="minutes"),
        "dhuhr": day.dhuhr.isoformat(timespec="minutes"),
        "asr": day.asr.isoformat(timespec="minutes"),
        "maghrib": day.maghrib.isoformat(timespec="minutes"),
        "isha": day.isha.isoformat(timespec="minutes"),
        "source": day.source.value,
        "fetched_at": day.fetched_at.isoformat(),
    }


def _entry_to_day(
    day: date, entry: Mapping[str, Any], top_zone: str, top_fetched: str
) -> PrayerDay:
    """Map one buffer entry; missing zone/fetched_at fall back to top-level.

    ``ValueError``/``KeyError`` from corrupt entries become ``ConfigError``
    at the repo methods (the buffer is a cache, but silent invented
    schedules are worse than a loud failure).
    """
    zone = str(entry.get("zone", top_zone))
    fetched_raw = entry.get("fetched_at", top_fetched)
    return PrayerDay(
        date=day,
        zone=zone,
        imsak=time.fromisoformat(str(entry["imsak"])),
        fajr=time.fromisoformat(str(entry["fajr"])),
        syuruq=time.fromisoformat(str(entry["syuruq"])),
        dhuha=time.fromisoformat(str(entry["dhuha"])),
        dhuhr=time.fromisoformat(str(entry["dhuhr"])),
        asr=time.fromisoformat(str(entry["asr"])),
        maghrib=time.fromisoformat(str(entry["maghrib"])),
        isha=time.fromisoformat(str(entry["isha"])),
        source=ScheduleSource(str(entry.get("source", ScheduleSource.JAKIM.value))),
        fetched_at=datetime.fromisoformat(str(fetched_raw)),
    )


def _top_defaults(data: Mapping[str, Any]) -> tuple[str, str]:
    """Top-level buffer zone/fetched_at fallbacks for sparse day entries."""
    zone = str(data.get("zone", ""))
    fetched = str(data.get("fetched_at", datetime.now(tz=UTC).isoformat()))
    return zone, fetched


def _corrupt(path: Path, key: str, exc: Exception) -> ConfigError:
    """One shape for buffer-day corruption errors (path + day + cause)."""
    return ConfigError(f"{path}: corrupt buffer day {key}: {exc}")


class FilePrayerRepo:
    """``PrayerRepo`` over config manual pins plus a JSON buffer cache.

    Manual pins (from ``config.schedule.manual_days``) always win over
    buffer rows for their date. ``delete_day`` mirrors the sqlite port —
    with one file-world caveat: pins live in the hand-edited config file,
    which this repo never writes, so deleting a pinned date returns False
    (remove the pin from the config instead); only a ``manual``-sourced
    buffer row with no config pin is actually removed.
    """

    def __init__(
        self, buffer_path: str | Path, manual_days: Sequence[ManualDay] = ()
    ) -> None:
        """Hold the buffer path plus a snapshot of the config manual pins."""
        self._buffer = Path(buffer_path)
        self._manual_days = tuple(manual_days)

    @property
    def buffer_path(self) -> Path:
        """Buffer file path (lets the lifespan watcher poll it)."""
        return self._buffer

    def validate_buffer(self) -> None:
        """Re-read and fully validate the buffer cache.

        Raises :class:`ConfigError` on any corrupt day entry (the
        watcher logs it and keeps serving; the next ``tick`` would
        surface it again, never blanking the display). Missing file
        is an empty cache, not an error.
        """
        data = self._read_buffer()
        raw_days = data.get("days", {})
        if not isinstance(raw_days, dict):
            return
        top_zone, top_fetched = _top_defaults(data)
        for key, entry in raw_days.items():
            try:
                candidate_date = date.fromisoformat(key)
            except ValueError as exc:
                raise _corrupt(self._buffer, key, exc) from exc
            if not isinstance(entry, Mapping):
                raise _corrupt(self._buffer, key, ValueError("not an object"))
            try:
                _entry_to_day(candidate_date, entry, top_zone, top_fetched)
            except (ValueError, KeyError) as exc:
                raise _corrupt(self._buffer, key, exc) from exc

    def set_manual_days(self, manual_days: Sequence[ManualDay]) -> None:
        """Refresh the in-memory manual-pin snapshot after a config reload.

        Buffer rows are read-through per call, so only the hand-edited
        ``manual_days`` snapshot needs swapping — the watcher calls this
        after a successful revalidation (invalid edits never reach here,
        keeping the last-good pins serving).
        """
        self._manual_days = tuple(manual_days)

    def _pin_for(self, day: date) -> ManualDay | None:
        """Return the config pin for ``day``, if one is hand-entered."""
        for pin in self._manual_days:
            if pin.date == day:
                return pin
        return None

    def _read_buffer(self) -> dict[str, Any]:
        """Load the buffer; a missing file is an empty cache, not an error."""
        if not self._buffer.exists():
            return {}
        try:
            text = self._buffer.read_text()
        except OSError as exc:
            raise ConfigError(
                f"{self._buffer}: cannot read prayer buffer: {exc}"
            ) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{self._buffer}: invalid JSON in prayer buffer: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{self._buffer}: prayer buffer must be an object")
        days = data.get("days", {})
        if not isinstance(days, dict):
            raise ConfigError(f"{self._buffer}: prayer buffer days must be an object")
        return data

    def _buffer_day(
        self, data: Mapping[str, Any], day: date, zone: str
    ) -> PrayerDay | None:
        """Return the buffered day for date+zone, else None."""
        days = data.get("days", {})
        if not isinstance(days, Mapping):
            return None
        entry = days.get(day.isoformat())
        if not isinstance(entry, Mapping):
            return None
        top_zone, top_fetched = _top_defaults(data)
        try:
            candidate = _entry_to_day(day, entry, top_zone, top_fetched)
        except (ValueError, KeyError) as exc:
            raise _corrupt(self._buffer, day.isoformat(), exc) from exc
        if candidate.zone != zone:
            return None
        return candidate

    def _write_buffer(
        self, days: dict[str, dict[str, str]], zone: str, fetched_at: str
    ) -> None:
        """Persist the buffer atomically (tmp+rename)."""
        _atomic_write_json(
            self._buffer,
            {
                "$schemaVersion": BUFFER_SCHEMA_VERSION,
                "zone": zone,
                "fetched_at": fetched_at,
                "days": days,
            },
        )

    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        """Return the manual pin first, else the buffered day, else None."""
        pin = self._pin_for(day)
        if pin is not None:
            return _manual_to_day(pin, zone)
        return self._buffer_day(self._read_buffer(), day, zone)

    def save_day(self, prayer_day: PrayerDay) -> None:
        """Upsert one day into the buffer (manual pins are never written here)."""
        data = self._read_buffer()
        raw_days = data.get("days", {})
        days: dict[str, dict[str, str]] = (
            dict(raw_days) if isinstance(raw_days, dict) else {}
        )
        days[prayer_day.date.isoformat()] = _day_to_entry(prayer_day)
        self._write_buffer(days, prayer_day.zone, prayer_day.fetched_at.isoformat())

    def save_day_unless_manual(self, prayer_day: PrayerDay) -> bool:
        """Upsert unless a manual pin (or manual buffer row) holds the date."""
        if self._pin_for(prayer_day.date) is not None:
            return False
        data = self._read_buffer()
        existing = self._buffer_day(data, prayer_day.date, prayer_day.zone)
        if existing is not None and existing.source is ScheduleSource.MANUAL:
            return False
        self.save_day(prayer_day)
        return True

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        """Newest manual pin or buffered day on or before ``day``, else None."""
        best: PrayerDay | None = None
        for pin in self._manual_days:
            if pin.date <= day and (best is None or pin.date >= best.date):
                best = _manual_to_day(pin, zone)
        data = self._read_buffer()
        raw_days = data.get("days", {})
        if isinstance(raw_days, dict):
            top_zone, top_fetched = _top_defaults(data)
            for key in sorted(raw_days):
                try:
                    candidate_date = date.fromisoformat(key)
                except ValueError as exc:
                    raise _corrupt(self._buffer, key, exc) from exc
                if candidate_date > day:
                    continue
                entry = raw_days[key]
                if not isinstance(entry, Mapping):
                    raise _corrupt(self._buffer, key, ValueError("not an object"))
                try:
                    candidate = _entry_to_day(
                        candidate_date, entry, top_zone, top_fetched
                    )
                except (ValueError, KeyError) as exc:
                    raise _corrupt(self._buffer, key, exc) from exc
                if candidate.zone != zone:
                    continue
                if best is None or candidate.date > best.date:
                    best = candidate
        return best

    def delete_day(self, day: date, zone: str) -> bool:
        """Delete a ``manual``-sourced buffer row; config pins return False."""
        if self._pin_for(day) is not None:
            return False
        data = self._read_buffer()
        raw_days = data.get("days", {})
        if not isinstance(raw_days, dict):
            return False
        entry = raw_days.get(day.isoformat())
        if not isinstance(entry, Mapping):
            return False
        top_zone, top_fetched = _top_defaults(data)
        try:
            candidate = _entry_to_day(day, entry, top_zone, top_fetched)
        except (ValueError, KeyError) as exc:
            raise _corrupt(self._buffer, day.isoformat(), exc) from exc
        if candidate.source is not ScheduleSource.MANUAL or candidate.zone != zone:
            return False
        days = dict(raw_days)
        del days[day.isoformat()]
        self._write_buffer(
            days,
            str(data.get("zone", zone)),
            str(data.get("fetched_at", candidate.fetched_at.isoformat())),
        )
        return True


def _playlist_from_file(entry: PlaylistFile) -> Playlist:
    """Map one file playlist; unknown anchors and bad windows fail loudly."""
    if entry.anchor_marker is None:
        anchor: MarkerName | None = None
    else:
        try:
            anchor = MarkerName(entry.anchor_marker)
        except ValueError:
            raise ConfigError(
                f"unknown playlist anchor marker: {entry.anchor_marker!r}"
            ) from None
    try:
        playlist = Playlist(
            id=entry.id,
            title=entry.title,
            active=entry.active,
            window_start=entry.window_start,
            window_end=entry.window_end,
            anchor_marker=anchor,
            anchor_start_offset_min=entry.anchor_start_offset_min,
            anchor_stop_offset_min=entry.anchor_stop_offset_min,
            cycle_mode=entry.cycle_mode,
            max_cycles=entry.max_cycles,
            items=tuple(
                PlaylistItem(
                    image_path=item.image_path,
                    duration_s=item.duration_s,
                    sort_order=item.sort_order,
                )
                for item in sorted(entry.items, key=lambda slot: slot.sort_order)
            ),
        )
        parse_window(playlist)
        return playlist
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


class FilePlaylistRepo:
    """Read-only playlist accessor over the hand-edited config file.

    Full CRUD is intentionally absent (mirroring the file-is-hand-edited
    contract): ``list``/``get`` serve the engine and display routes, which
    re-read the file on every call so edits apply without a restart.
    """

    def __init__(self, config_path: str | Path) -> None:
        """Hold the config file path (reads are fresh on every call)."""
        self._path = Path(config_path)

    def list(self) -> list[Playlist]:
        """Return every playlist in file order."""
        entries = load_config_file(self._path).playlists
        return [_playlist_from_file(entry) for entry in entries]

    def get(self, playlist_id: str) -> Playlist | None:
        """Return one playlist by id, else None."""
        for playlist in self.list():
            if playlist.id == playlist_id:
                return playlist
        return None
