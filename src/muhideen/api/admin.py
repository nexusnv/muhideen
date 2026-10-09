"""Admin cross-cutting: token auth, audit, shared write lock, error shape.

Foundation for the token-gated admin surface (spec §1): every admin
write lives on ``admin_router`` (Bearer-gated + audited by
construction) and holds ``get_write_lock`` around read + merge +
validate + rename. Config section PATCH/validate routes (§2) and
playlist/item writes (§3) live here; public config GETs live on
``public_config_router`` and public playlist reads + preview on
``public_playlist_router`` (spec §1 reads stay public, so no gate
and no audit).
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import zipfile
import zlib
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from muhideen.adapters.file_config import (
    FilePrayerRepo,
    _atomic_write_json,  # pyright: ignore[reportPrivateUsage]
    _atomic_write_json_list,  # pyright: ignore[reportPrivateUsage]
    _playlist_from_file,  # pyright: ignore[reportPrivateUsage]
    _settings_from_config,  # pyright: ignore[reportPrivateUsage]
    load_config_file,
    load_manual_days_file,
    manual_days_file_for_config,
    resolve_pins_path,
)
from muhideen.adapters.file_models import (
    REQUIRED_IQAMAH_PRAYERS,
    ConfigFile,
    Display,
    DisplayTheme,
    IqamahRuleFile,
    Language,
    ManualDay,
    PlaylistFile,
    TimeHHMM,
    ensure_unique_manual_dates,
)
from muhideen.adapters.media_store import (
    UPFRONT_SLACK_BYTES,
    MediaTooLargeError,
    atomic_write_bytes,
    cap_for_kind,
    dest_is_file,
    destination_for_kind,
    list_media_files,
    media_path_for_rel,
    normalize_media_rel,
    read_upload_bounded_sync,
    reencode_image,
    sanitize_upload_basename,
    verify_audio_mp3,
)
from muhideen.core.errors import ConfigError
from muhideen.core.values import (
    AsrJuristic,
    SyncProvider,
    ThemeBoundaryStrip,
    ThemeClockFormat,
    ThemeCountdownStyle,
    ThemeDensity,
    ThemeFont,
    ThemeHijriForm,
    ThemePalette,
    strip_legacy_media_prefix,
)
from muhideen.domain.playlist_window import parse_window
from muhideen.domain.stage import resolve_stage, stage_id

logger = logging.getLogger(__name__)

ADMIN_WRITES_DISABLED = "admin writes disabled"
"""503 detail when no usable token was read at boot (vs broken-config 503s)."""

INVALID_ADMIN_TOKEN = "invalid admin token"
"""401 detail for missing/mismatched/empty Bearer credentials."""

_bearer = HTTPBearer(auto_error=False)
"""Bearer extractor that yields ``None`` (never the default 403s)."""


def admin_token_file_for_config(config_path: str | Path) -> Path:
    """Single rule for the token file: always ``<config-dir>/admin_token``."""
    return Path(config_path).parent / "admin_token"


def read_admin_token(path: str | Path | None) -> str | None:
    """Read-once token load at boot; missing/blank/unreadable → ``None``.

    A blank file never yields a comparable token (no empty-Bearer
    bypass). Callers keep the result in memory; post-boot file changes
    have no effect until restart.
    """
    if path is None:
        return None
    candidate = Path(path)
    try:
        text = candidate.read_text()
    except (OSError, UnicodeError) as exc:
        logger.warning(
            "admin token file %s unreadable (%s): admin writes disabled",
            candidate,
            exc,
        )
        return None
    token = text.strip()
    if not token:
        logger.warning("admin token file %s is empty: admin writes disabled", candidate)
        return None
    return token


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Gate an admin endpoint: disabled-check first, then Bearer compare.

    A request carrying any token while disabled still gets ``503``, never
    ``401``. Missing/mismatched/empty credentials get ``401`` with the
    ``WWW-Authenticate: Bearer`` challenge. The expected token lives on
    ``request.app.state`` (set by ``create_app`` from ``AppDeps``).
    """
    expected: str | None = getattr(request.app.state, "admin_token", None)
    if not expected:
        raise HTTPException(status_code=503, detail=ADMIN_WRITES_DISABLED)
    provided = credentials.credentials if credentials is not None else ""
    # Byte-wise compare: str compare_digest raises TypeError on non-ASCII
    # (500); UTF-8 bytes compare so any non-ASCII Bearer is a 401 mismatch.
    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail=INVALID_ADMIN_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AdminRoute(APIRoute):
    """Audited admin route: INFO-logs peer/method/path/status, never tokens.

    Only the path is logged (never query or headers), so the Bearer token
    cannot leak into journald. Denials (``require_admin`` 401/503) and
    body-validation 422s are raised as exceptions by the handler, so they
    are logged and re-raised — every gated outcome is audited, including
    unexpected 500s.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap the handler with post-response audit logging."""
        handler = super().get_route_handler()

        async def _audited(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                _audit_log(request, 422)
                raise
            except HTTPException as exc:
                _audit_log(request, exc.status_code)
                raise
            except Exception:
                _audit_log(request, 500)
                raise
            _audit_log(request, response.status_code)
            return response

        return _audited


def _audit_log(request: Request, status_code: int) -> None:
    """Emit one audit line for a gated request (no tokens, path only)."""
    peer = request.client.host if request.client is not None else "unknown"
    logger.info("%s %s %s -> %s", peer, request.method, request.url.path, status_code)


def get_write_lock(request: Request) -> threading.Lock:
    """Shared process-wide config write lock for one admin request.

    Every admin write holds this (not the per-instance repo locks, which
    do not serialize separate instances) around read + merge + validate
    + rename. ``workers=1`` makes in-process locking sufficient. Write
    endpoints stay sync ``def`` — never hold this inside ``async def``.
    A missing lock is server misconfiguration: fail safe to 503 (deny the
    write) rather than 500 or an unsynchronized write.
    """
    lock: threading.Lock | None = getattr(request.app.state, "write_lock", None)
    if lock is None:
        raise HTTPException(status_code=503, detail=ADMIN_WRITES_DISABLED)
    return lock


def invalid(field: str | Sequence[str], message: str) -> HTTPException:
    """422 for mapped ``ValueError``s (Settings/parse_window/ZoneInfo/...).

    Uses the ``["body", <field>, ...]`` loc convention (nested:
    ``["body", "aladhan", "base_url"]``) so mapped errors share FastAPI's
    ``{detail: [{loc, msg, type}]}`` shape instead of leaking as 500s.
    """
    loc: list[str] = ["body", field] if isinstance(field, str) else ["body", *field]
    return HTTPException(
        status_code=422,
        detail=[{"loc": loc, "msg": message, "type": "value_error"}],
    )


admin_router = APIRouter(route_class=AdminRoute, dependencies=[Depends(require_admin)])
"""Gated admin router: writes + dry-run validates (audited, Bearer)."""

public_config_router = APIRouter()
"""Public config reads (spec §1): GETs stay unauthenticated, no audit."""


# --- Per-section config PATCH + GET + validate (spec §2). ---

_ABS_PATH_RE = re.compile(r"/[^\s\"']*")
"""Absolute-path scrubber (mirrors api.app; import would cycle)."""


def _scrub_config_error(message: str) -> str:
    """Scrub absolute paths from a config error for the wire.

    Mirrors ``api.app._public_detail`` (which cannot be imported here:
    app imports this module). Domain messages without a path prefix pass
    through so callers keep the cause.
    """
    path, sep, rest = message.partition(": ")
    if not sep or not Path(path).is_absolute():
        return message
    scrubbed = _ABS_PATH_RE.sub("<path>", rest)
    return f"config: {scrubbed}" if scrubbed else "config: invalid configuration"


class _Partial(BaseModel):
    """All-optional PATCH partial: omitted = unchanged, extra keys 422."""

    model_config = ConfigDict(extra="forbid")


class MasjidPatch(_Partial):
    """Masjid partial: display name + IANA timezone (both non-nullable)."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    timezone: str | None = None


class JakimPatch(_Partial):
    """Nested jakim partial: fetch zone clears on explicit null."""

    zone: Annotated[str, Field(min_length=1, max_length=32)] | None = None


class AladhanPatch(_Partial):
    """Nested aladhan partial: base URL + method id (both non-nullable)."""

    base_url: str | None = None
    method: Annotated[int, Field(ge=0, le=23)] | None = None


class SchedulePatch(_Partial):
    """Schedule partial: provider switches, zone/coords/offsets, pins ref."""

    sync_provider: SyncProvider | None = None
    zone: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    jakim: JakimPatch | None = None
    aladhan: AladhanPatch | None = None
    method: Literal["MABIMS", "MWL", "ISNA", "Egyptian"] | None = None
    asr_juristic: AsrJuristic | None = None
    lat: Annotated[float, Field(ge=-90, le=90)] | None = None
    lon: Annotated[float, Field(ge=-180, le=180)] | None = None
    calc_only: bool | None = None
    hijri_offset: Annotated[int, Field(ge=-2, le=2)] | None = None
    imsak_offset_min: Annotated[int, Field(ge=0, le=10)] | None = None
    dhuha_offset_min: Annotated[int, Field(ge=15, le=30)] | None = None
    boundary_countdown: bool | None = None
    manual_days_file: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class TimingPatch(_Partial):
    """Timing partial: overrides/iqamah_rules replace wholesale when sent."""

    adhan_duration_s: Annotated[int, Field(gt=0)] | None = None
    dim_minutes_default: Annotated[int, Field(ge=5, le=60)] | None = None
    dim_minutes_jumuah: Annotated[int, Field(ge=5, le=60)] | None = None
    countdown_before_adhan_min: Annotated[int, Field(ge=0, le=90)] | None = None
    countdown_before_adhan_overrides: (
        dict[str, Annotated[int, Field(ge=0, le=90)]] | None
    ) = None
    iqamah_rules: list[IqamahRuleFile] | None = None


class ThemePatch(_Partial):
    """Theme partial: seven closed enums, all non-nullable (null → 422)."""

    palette: ThemePalette | None = None
    font: ThemeFont | None = None
    countdown_style: ThemeCountdownStyle | None = None
    clock_format: ThemeClockFormat | None = None
    hijri_form: ThemeHijriForm | None = None
    boundary_strip: ThemeBoundaryStrip | None = None
    density: ThemeDensity | None = None


class AdhanAudioPatch(_Partial):
    """Adhan-audio partial: quiet bounds paired-or-null; file path-string."""

    enabled: bool | None = None
    volume: Annotated[int, Field(ge=0, le=100)] | None = None
    quiet_hours_start: TimeHHMM | None = None
    quiet_hours_end: TimeHHMM | None = None
    muted_prayers: list[str] | None = None
    file: str | None = None


_PRAYER_ONLY = frozenset(REQUIRED_IQAMAH_PRAYERS)
"""The six markers that carry adhan/iqamah/countdown (jumuah distinct)."""

_BOUNDARY_MARKERS = frozenset({"imsak", "syuruq", "dhuha"})
"""Informational markers: never an override, mute, or iqamah target."""

_SECTION_ATTRS = {
    "masjid": "masjid",
    "schedule": "schedule",
    "timing": "timing",
    "theme": "theme",
    "adhan-audio": "adhan_audio",
}
"""URL section names to ConfigFile attribute names."""


def _reject_explicit_nulls(
    patch: BaseModel, nullable: set[str], *, prefix: tuple[str, ...] = ()
) -> None:
    """Reject explicit null for non-nullable keys (omitted stays unchanged)."""
    for key in patch.model_fields_set:
        if getattr(patch, key) is None and key not in nullable:
            dotted = ".".join((*prefix, key))
            raise invalid((*prefix, key), f"{dotted} must not be null")


def _stripped(field: str | tuple[str, ...], value: str) -> str:
    """Strip free text; whitespace-only is 422 (would route as unset)."""
    text = value.strip()
    if not text:
        dotted = field if isinstance(field, str) else ".".join(field)
        raise invalid(field, f"{dotted} must not be blank")
    return text


def _check_timezone(value: str) -> None:
    """Validate an IANA timezone now (ZoneInfo failures become 422)."""
    try:
        ZoneInfo(value)
    except (ValueError, TypeError, ZoneInfoNotFoundError, KeyError) as exc:
        raise invalid("timezone", f"unknown timezone: {value!r}") from exc


def _check_override_key(key: str) -> None:
    """Enforce prayer-only countdown-override keys (Settings rule, precise loc)."""
    if key in _PRAYER_ONLY:
        return
    loc = ("countdown_before_adhan_overrides", key)
    if key in _BOUNDARY_MARKERS:
        raise invalid(
            loc,
            f"boundary time marker cannot have a countdown override: {key}",
        )
    raise invalid(loc, f"unknown prayer for countdown override: {key!r}")


def _check_muted_prayer(value: str) -> None:
    """Enforce prayer-only muted prayers (Settings rule, precise loc)."""
    if value in _PRAYER_ONLY:
        return
    loc = ("muted_prayers", value)
    if value in _BOUNDARY_MARKERS:
        raise invalid(loc, f"boundary time marker cannot mute adhan: {value}")
    raise invalid(loc, f"unknown prayer for adhan mute: {value!r}")


def _config_path(request: Request) -> Path:
    """File backing this app's config; missing state fails safe to 503."""
    path = getattr(request.app.state, "config_path", None)
    if path is None:
        raise HTTPException(status_code=503, detail="config: unavailable")
    return Path(path)


def _read_raw_config(config_path: Path) -> dict[str, Any]:
    """Parse the config file as a raw dict; breakage is 503 (scrubbed)."""
    try:
        text = config_path.read_text()
    except (OSError, UnicodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"{config_path}: cannot read config: {exc}"),
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"{config_path}: invalid JSON: {exc}"),
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"{config_path}: config root must be object"),
        )
    return cast(dict[str, Any], data)


def _write_config_json(config_path: Path, merged: dict[str, Any]) -> None:
    """Atomic config rename; disk faults are 503 (never 500)."""
    try:
        _atomic_write_json(config_path, merged)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(
                f"{config_path}: cannot write config file: {exc}"
            ),
        ) from exc


def _section_dict(raw: dict[str, Any], section: str) -> dict[str, Any]:
    """Current raw section (missing/non-object heals through validation)."""
    current = raw.get(section)
    if isinstance(current, dict):
        return cast(dict[str, Any], current)
    return {}


def _apply_changes(
    section: dict[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge one section with the fixed §2 depth rule.

    One-level deep merge for nested ``jakim``/``aladhan`` (sibling keys
    survive); full replace for everything else (overrides, iqamah_rules,
    scalars, and explicit nulls clearing nullable keys).
    """
    merged = dict(section)
    for key, value in changes.items():
        if key in ("jakim", "aladhan") and isinstance(value, dict):
            inner: dict[str, Any] = {}
            nested = merged.get(key)
            if isinstance(nested, dict):
                inner.update(cast(dict[str, Any], nested))
            inner.update(cast(dict[str, Any], value))
            merged[key] = inner
        else:
            merged[key] = value
    return merged


def _mapped_422(
    exc: ValidationError, section: str | None, patch_keys: Sequence[str]
) -> HTTPException:
    """Map a full-file ValidationError to 422 [{loc, msg}] (never 500).

    Locs are file-rooted (``("schedule", "jakim", "zone")``); the patched
    section prefix is stripped so locs read body-relative
    (``["body", "jakim", "zone"]``). Section-root failures (model
    validators) fall back to the first patched key.
    """
    detail: list[dict[str, Any]] = []
    for err in exc.errors():
        loc: list[str | int] = list(err["loc"])
        if section is not None and loc and loc[0] == section:
            loc = loc[1:]
        if not loc:
            loc = list(patch_keys[:1])
        detail.append({"loc": ["body", *loc], "msg": err["msg"], "type": err["type"]})
    return HTTPException(status_code=422, detail=detail)


def _validate_merged(
    merged: dict[str, Any], section: str | None, patch_keys: Sequence[str]
) -> ConfigFile:
    """Full-file ConfigFile + Settings validation before any rename.

    ``ConfigFile`` alone misses file-model gaps (boundary override keys
    are untyped there); the ``Settings`` conversion catches them, so both
    run before the file is touched. Invalid never touches disk.
    """
    try:
        cfg = ConfigFile.model_validate(merged)
    except ValidationError as exc:
        raise _mapped_422(exc, section, patch_keys) from exc
    try:
        _settings_from_config(cfg)
    except ValueError as exc:
        if section is None:
            raise invalid([], str(exc)) from exc
        raise invalid(section, str(exc)) from exc
    return cfg


def _effective_zone(section: Mapping[str, Any]) -> str:
    """Served-zone label for a raw schedule section (mirrors the model)."""
    zone = section.get("zone")
    if isinstance(zone, str) and zone:
        return zone
    nested = section.get("jakim")
    if isinstance(nested, dict):
        fetch_key = cast(dict[str, Any], nested).get("zone")
        if isinstance(fetch_key, str) and fetch_key:
            return fetch_key
    return "local"


def _publish_config_update(request: Request) -> None:
    """Fan out ``config-update`` after a successful write (no new channel)."""
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        bus.publish("config-update", ("settings",))


def _check_pins(
    request: Request,
    config_path: Path,
    merged_section: Mapping[str, Any],
    cfg: ConfigFile,
    patch_keys: Sequence[str],
) -> None:
    """Run buffer-completion pins validation for an affected schedule merge.

    Inline pins come from the validated merge; a pins-file ref resolves
    against the config dir and loads from disk (absent/unparsable names
    the ref). Failures are 422 with the ref or zone as loc, never 500.
    """
    ref = merged_section.get("manual_days_file")
    pins: Sequence[ManualDay]
    if ref is not None:
        pins_path = resolve_pins_path(config_path, ref)
        try:
            pins = load_manual_days_file(pins_path)
        except ConfigError as exc:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "manual_days_file"],
                        "msg": _scrub_config_error(str(exc)),
                        "type": "value_error",
                    }
                ],
            ) from exc
        loc: Sequence[str] = ("manual_days_file",)
    else:
        pins = list(cfg.schedule.manual_days)
        if "zone" in patch_keys:
            loc = ("zone",)
        elif "jakim" in patch_keys:
            loc = ("jakim", "zone")
        else:
            loc = ()
    repo = getattr(request.app.state, "prayer_repo", None)
    if not isinstance(repo, FilePrayerRepo):
        return
    try:
        repo.validate_pins(pins, cfg.schedule.effective_zone)
    except ConfigError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["body", *loc],
                    "msg": _scrub_config_error(str(exc)),
                    "type": "value_error",
                }
            ],
        ) from exc


def _merge_validate_write(
    request: Request,
    section: str,
    patch: BaseModel,
    changes: Mapping[str, Any],
    *,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Shared write path: lock + read + merge + validate (+pins) + rename.

    Returns the old section and the merged section so callers can derive
    ``restart_required``. Dry-run validates identically but skips the
    rename and the publish.
    """
    provided = tuple(sorted(patch.model_fields_set))
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        old_section = _section_dict(raw, section)
        merged_section = _apply_changes(old_section, changes)
        merged = dict(raw)
        merged[section] = merged_section
        cfg = _validate_merged(merged, section, provided)
        if section == "schedule" and (
            "manual_days_file" in provided
            or _effective_zone(old_section) != _effective_zone(merged_section)
        ):
            _check_pins(request, config_path, merged_section, cfg, provided)
        if not dry_run:
            _write_config_json(config_path, merged)
            _publish_config_update(request)
    return old_section, merged_section


def _masjid_changes(patch: MasjidPatch) -> dict[str, Any]:
    """Pure masjid checks: strip-then-reject-blank name, known timezone."""
    _reject_explicit_nulls(patch, set())
    changes: dict[str, Any] = {}
    if "name" in patch.model_fields_set and patch.name is not None:
        changes["name"] = _stripped("name", patch.name)
    if "timezone" in patch.model_fields_set and patch.timezone is not None:
        _check_timezone(patch.timezone)
        changes["timezone"] = patch.timezone
    return changes


def _schedule_changes(patch: SchedulePatch) -> dict[str, Any]:
    """Pure schedule checks: strip free text, deep-merge nested objects."""
    _reject_explicit_nulls(patch, {"zone", "lat", "lon", "manual_days_file"})
    changes: dict[str, Any] = {}
    for key in (
        "sync_provider",
        "method",
        "asr_juristic",
        "lat",
        "lon",
        "calc_only",
        "hijri_offset",
        "imsak_offset_min",
        "dhuha_offset_min",
        "boundary_countdown",
    ):
        if key in patch.model_fields_set:
            changes[key] = getattr(patch, key)
    if "zone" in patch.model_fields_set:
        changes["zone"] = (
            _stripped("zone", patch.zone) if patch.zone is not None else None
        )
    if "manual_days_file" in patch.model_fields_set:
        changes["manual_days_file"] = (
            _stripped("manual_days_file", patch.manual_days_file)
            if patch.manual_days_file is not None
            else None
        )
    if "jakim" in patch.model_fields_set and patch.jakim is not None:
        _reject_explicit_nulls(patch.jakim, {"zone"}, prefix=("jakim",))
        nested: dict[str, Any] = {}
        if "zone" in patch.jakim.model_fields_set:
            nested["zone"] = (
                _stripped(("jakim", "zone"), patch.jakim.zone)
                if patch.jakim.zone is not None
                else None
            )
        changes["jakim"] = nested
    if "aladhan" in patch.model_fields_set and patch.aladhan is not None:
        _reject_explicit_nulls(patch.aladhan, set(), prefix=("aladhan",))
        nested = {}
        if "base_url" in patch.aladhan.model_fields_set:
            nested["base_url"] = patch.aladhan.base_url
        if "method" in patch.aladhan.model_fields_set:
            nested["method"] = patch.aladhan.method
        changes["aladhan"] = nested
    return changes


def _timing_changes(patch: TimingPatch) -> dict[str, Any]:
    """Pure timing checks: prayer-only override keys, wholesale maps."""
    _reject_explicit_nulls(patch, set())
    changes: dict[str, Any] = {}
    for key in (
        "adhan_duration_s",
        "dim_minutes_default",
        "dim_minutes_jumuah",
        "countdown_before_adhan_min",
    ):
        if key in patch.model_fields_set:
            changes[key] = getattr(patch, key)
    if "countdown_before_adhan_overrides" in patch.model_fields_set:
        overrides = patch.countdown_before_adhan_overrides or {}
        for key in overrides:
            _check_override_key(key)
        changes["countdown_before_adhan_overrides"] = dict(overrides)
    if "iqamah_rules" in patch.model_fields_set:
        changes["iqamah_rules"] = [
            rule.model_dump() for rule in patch.iqamah_rules or []
        ]
    return changes


def _theme_changes(patch: ThemePatch) -> dict[str, Any]:
    """Pure theme checks: closed enums, explicit null rejected."""
    _reject_explicit_nulls(patch, set())
    return {key: getattr(patch, key) for key in patch.model_fields_set}


def _adhan_changes(patch: AdhanAudioPatch) -> dict[str, Any]:
    """Pure adhan-audio checks except the file (needs effective enabled)."""
    _reject_explicit_nulls(patch, {"quiet_hours_start", "quiet_hours_end"})
    changes: dict[str, Any] = {}
    for key in ("enabled", "volume", "quiet_hours_start", "quiet_hours_end"):
        if key in patch.model_fields_set:
            changes[key] = getattr(patch, key)
    if "muted_prayers" in patch.model_fields_set:
        muted = list(patch.muted_prayers or [])
        for value in muted:
            _check_muted_prayer(value)
        changes["muted_prayers"] = muted
    if "file" in patch.model_fields_set:
        changes["file"] = patch.file
    return changes


def _schedule_restart(
    old_section: Mapping[str, Any], merged_section: Mapping[str, Any]
) -> bool:
    """Restart only for effective-sync-client changes.

    A ``sync_provider`` flip always restarts; ``aladhan`` edits restart
    only while the effective provider is ``aladhan`` (validated no-op
    otherwise). URL comparison ignores a trailing slash (model strips).
    """
    if merged_section.get("sync_provider") != old_section.get("sync_provider"):
        return True
    if merged_section.get("sync_provider") != "aladhan":
        return False
    old_al = old_section.get("aladhan")
    new_al = merged_section.get("aladhan")
    old_map = cast(dict[str, Any], old_al) if isinstance(old_al, dict) else {}
    new_map = cast(dict[str, Any], new_al) if isinstance(new_al, dict) else {}

    def _norm(url: Any) -> Any:
        return url.rstrip("/") if isinstance(url, str) else url

    return _norm(old_map.get("base_url")) != _norm(
        new_map.get("base_url")
    ) or old_map.get("method") != new_map.get("method")


@admin_router.patch("/api/config/masjid")
def patch_masjid(request: Request, patch: MasjidPatch) -> dict[str, Any]:
    """Partial masjid update; restart only when the timezone changed."""
    changes = _masjid_changes(patch)
    old_section, merged_section = _merge_validate_write(
        request, "masjid", patch, changes, dry_run=False
    )
    restart = "timezone" in patch.model_fields_set and old_section.get(
        "timezone"
    ) != merged_section.get("timezone")
    return {"ok": True, "restart_required": restart}


@admin_router.post("/api/config/masjid/validate")
def validate_masjid(request: Request, patch: MasjidPatch) -> dict[str, bool]:
    """Dry-run masjid partial merged over the current file (writes nothing)."""
    _merge_validate_write(
        request, "masjid", patch, _masjid_changes(patch), dry_run=True
    )
    return {"ok": True}


@admin_router.patch("/api/config/schedule")
def patch_schedule(request: Request, patch: SchedulePatch) -> dict[str, Any]:
    """Partial schedule update; restart only for sync-client changes."""
    old_section, merged_section = _merge_validate_write(
        request, "schedule", patch, _schedule_changes(patch), dry_run=False
    )
    return {
        "ok": True,
        "restart_required": _schedule_restart(old_section, merged_section),
    }


@admin_router.post("/api/config/schedule/validate")
def validate_schedule(request: Request, patch: SchedulePatch) -> dict[str, bool]:
    """Dry-run schedule partial merged over the current file (writes nothing)."""
    _merge_validate_write(
        request, "schedule", patch, _schedule_changes(patch), dry_run=True
    )
    return {"ok": True}


@admin_router.patch("/api/config/timing")
def patch_timing(request: Request, patch: TimingPatch) -> dict[str, Any]:
    """Partial timing update (hot-reloads; never needs a restart)."""
    _merge_validate_write(
        request, "timing", patch, _timing_changes(patch), dry_run=False
    )
    return {"ok": True, "restart_required": False}


@admin_router.post("/api/config/timing/validate")
def validate_timing(request: Request, patch: TimingPatch) -> dict[str, bool]:
    """Dry-run timing partial merged over the current file (writes nothing)."""
    _merge_validate_write(
        request, "timing", patch, _timing_changes(patch), dry_run=True
    )
    return {"ok": True}


@admin_router.patch("/api/config/theme")
def patch_theme(request: Request, patch: ThemePatch) -> dict[str, Any]:
    """Partial theme update (hot-reloads; never needs a restart)."""
    _merge_validate_write(request, "theme", patch, _theme_changes(patch), dry_run=False)
    return {"ok": True, "restart_required": False}


@admin_router.post("/api/config/theme/validate")
def validate_theme(request: Request, patch: ThemePatch) -> dict[str, bool]:
    """Dry-run theme partial merged over the current file (writes nothing)."""
    _merge_validate_write(request, "theme", patch, _theme_changes(patch), dry_run=True)
    return {"ok": True}


@admin_router.patch("/api/config/adhan-audio")
def patch_adhan_audio(request: Request, patch: AdhanAudioPatch) -> dict[str, Any]:
    """Partial adhan-audio update (hot-reloads; never needs a restart).

    Only the file knob is skipped while disabled; volume, quiet hours,
    and mutes validate regardless of the master switch.
    """
    _merge_validate_write(
        request, "adhan_audio", patch, _adhan_changes(patch), dry_run=False
    )
    return {"ok": True, "restart_required": False}


@admin_router.post("/api/config/adhan-audio/validate")
def validate_adhan_audio(request: Request, patch: AdhanAudioPatch) -> dict[str, bool]:
    """Dry-run adhan-audio partial merged over the file (writes nothing)."""
    _merge_validate_write(
        request, "adhan_audio", patch, _adhan_changes(patch), dry_run=True
    )
    return {"ok": True}


@admin_router.post("/api/config/validate")
def validate_full(request: Request, candidate: ConfigFile) -> dict[str, bool]:
    """Dry-run full-file candidate (export/import round-trip; writes nothing).

    The body is a complete ``ConfigFile`` (``$schemaVersion`` alias
    spelling required); structural, ``Settings``, and pins checks all run.
    """
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        try:
            _settings_from_config(candidate)
        except ValueError as exc:
            raise invalid([], str(exc)) from exc
        ref = candidate.schedule.manual_days_file
        pins: Sequence[ManualDay] = list(candidate.schedule.manual_days)
        loc: Sequence[str] = ("schedule", "manual_days")
        if ref is not None:
            try:
                pins = load_manual_days_file(resolve_pins_path(config_path, ref))
            except ConfigError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        {
                            "loc": ["body", "schedule", "manual_days_file"],
                            "msg": _scrub_config_error(str(exc)),
                            "type": "value_error",
                        }
                    ],
                ) from exc
            loc = ("schedule", "manual_days_file")
        repo = getattr(request.app.state, "prayer_repo", None)
        if isinstance(repo, FilePrayerRepo):
            try:
                repo.validate_pins(pins, candidate.schedule.effective_zone)
            except ConfigError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=[
                        {
                            "loc": ["body", *loc],
                            "msg": _scrub_config_error(str(exc)),
                            "type": "value_error",
                        }
                    ],
                ) from exc
    return {"ok": True}


@public_config_router.get("/api/config")
def get_full_config(request: Request) -> dict[str, Any]:
    """Serve the whole file, reserialized with ``$schemaVersion`` (export)."""
    cfg = load_config_file(_config_path(request))
    return cfg.model_dump(mode="json", by_alias=True)


@public_config_router.get("/api/config/manual-days")
def get_manual_days(request: Request) -> dict[str, Any]:
    """Serve the effective pins plus which store serves them (public read).

    Registered before ``/{section}``: the generic section route would
    otherwise claim ``manual-days`` as an unknown section (404). A
    set-but-absent pins ref is 503 with a hand-fix detail (same
    fail-loud as ``load_config_file``) — never an empty ``pins: []``
    masking the missing file.
    """
    config_path = _config_path(request)
    raw = _read_raw_config(config_path)
    section = _manual_schedule_section(raw)
    ref = section.get("manual_days_file")
    pins_path = manual_days_file_for_config(config_path)
    if pins_path is None:
        cfg = load_config_file(config_path)
        return {
            "source": "inline",
            "pins": [pin.model_dump(mode="json") for pin in cfg.schedule.manual_days],
        }
    if isinstance(section.get("manual_days"), list) and section["manual_days"]:
        raise HTTPException(
            status_code=503,
            detail="schedule.manual_days and schedule.manual_days_file "
            "are exclusive — hand-fix the config file until one is cleared",
        )
    try:
        pins = load_manual_days_file(pins_path)
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                _pins_file_detail(ref, exc)
                if isinstance(ref, str)
                else _scrub_config_error(str(exc))
            ),
        ) from exc
    return {
        "source": "file",
        "pins": [pin.model_dump(mode="json") for pin in pins],
    }


@public_config_router.get("/api/config/{section}")
def get_config_section(request: Request, section: str) -> Any:
    """Serve one parsed section; unknown names are 404."""
    attr = _SECTION_ATTRS.get(section)
    if attr is None:
        raise HTTPException(
            status_code=404, detail=f"unknown config section: {section!r}"
        )
    cfg = load_config_file(_config_path(request))
    return getattr(cfg, attr).model_dump(mode="json", by_alias=True)


# --- Playlists + items + preview (spec §3). ---

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""Shared id grammar: writes only; legacy rows list as-is (slash: routing-404)."""

public_playlist_router = APIRouter()
"""Public playlist reads (spec §1): list, detail, preview stay open, no audit."""


class PlaylistItemCreate(_Partial):
    """Item create: path + seconds required; order auto-assigns when omitted."""

    image_path: str
    duration_s: Annotated[int, Field(gt=0)]
    sort_order: Annotated[int, Field(ge=0)] | None = None


class PlaylistCreate(_Partial):
    """Playlist create: identity required, the rest files in with defaults."""

    id: str
    title: str
    active: bool = True
    window_start: str | None = None
    window_end: str | None = None
    anchor_marker: str | None = None
    anchor_start_offset_min: int = 0
    anchor_stop_offset_min: int = 0
    cycle_mode: Literal["indefinite", "repeat"] = "indefinite"
    max_cycles: int | None = None
    items: list[PlaylistItemCreate] = Field(default_factory=list[PlaylistItemCreate])


class PlaylistPatch(_Partial):
    """Playlist partial: ``id`` rename and ``items`` ride other endpoints."""

    title: str | None = None
    active: bool | None = None
    window_start: str | None = None
    window_end: str | None = None
    anchor_marker: str | None = None
    anchor_start_offset_min: int | None = None
    anchor_stop_offset_min: int | None = None
    cycle_mode: Literal["indefinite", "repeat"] | None = None
    max_cycles: int | None = None


def _require_tz_aware_moment(value: datetime) -> datetime:
    """Reject naive preview moments; require a UTC offset (like next-event)."""
    if value.tzinfo is None:
        raise ValueError("moment must include a UTC offset (tz-aware ISO8601)")
    return value


def _check_playlist_id(value: str) -> str:
    """Strip + enforce the shared id grammar on a new value (422).

    ``preview`` stays creatable: the static ``GET /api/playlists/preview``
    route is registered before ``GET /api/playlists/{id}`` so it always
    wins for reads (pinned by ``test_preview_not_shadowed_by_id_named_preview``);
    the row itself remains visible in list + mutable via PATCH/DELETE.
    """
    text = value.strip()
    if not text:
        raise invalid("id", "id must not be blank")
    if _ID_RE.match(text) is None:
        raise invalid("id", f"id must be 1-64 [A-Za-z0-9_-]: {value!r}")
    return text


def _normalize_image_path(value: str) -> str:
    """Media-relative item path (``normalize_adhan_rel`` rules, dangling ok).

    Existence is NOT required: files may be uploaded before or after the
    item is created. Raises ``ValueError`` (mapped to 422) on blank,
    NUL, backslash, directory-like, absolute/drive-letter, URL-structural,
    or escaping values; a legacy ``media/`` prefix is stripped.
    """
    if not value or not value.strip():
        raise ValueError("playlist item needs an image path")
    if "\x00" in value:
        raise ValueError(f"image_path must not contain NUL bytes: {value!r}")
    if "\\" in value:
        raise ValueError(f"image_path must not contain backslashes: {value!r}")
    candidate = value
    if candidate.strip().endswith("/"):
        raise ValueError(f"image_path must name a file, not a directory: {value!r}")
    if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        raise ValueError(f"image_path must be relative: {value!r}")
    rel = strip_legacy_media_prefix(candidate)
    if rel.startswith("/"):
        raise ValueError(f"image_path must be relative: {value!r}")
    if "?" in rel or "#" in rel:
        raise ValueError(f"image_path must not contain '?' or '#': {value!r}")
    norm = posixpath.normpath(rel)
    if norm in ("", "."):
        raise ValueError(f"image_path must name a file: {value!r}")
    if norm == ".." or norm.startswith("../"):
        raise ValueError(f"image_path escapes the media root: {value!r}")
    return norm


def _check_item_slot(image_path: str, duration_s: int) -> dict[str, Any]:
    """Normalize one item's path; types/ranges already fell closed in parsing."""
    try:
        rel = _normalize_image_path(image_path)
    except ValueError as exc:
        raise invalid("image_path", str(exc)) from exc
    return {"image_path": rel, "duration_s": duration_s}


def _assign_create_orders(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Auto-assign missing orders (max+1, empty → 0); within-body dupes → 422.

    Two-pass so assignment never depends on item order: explicit orders
    are collected (and deduped) first, then each missing order takes the
    next free value at/above ``max(explicit)+1``. ``[{auto}, {0}]`` yields
    ``[1, 0]`` instead of a spurious duplicate.
    """
    seen: set[int] = set()
    for slot in slots:
        order = slot["sort_order"]
        if order is not None:
            if order in seen:
                raise invalid(
                    "sort_order", f"duplicate sort_order in playlist: {order!r}"
                )
            seen.add(order)
    next_order = (max(seen) + 1) if seen else 0
    for slot in slots:
        if slot["sort_order"] is None:
            while next_order in seen:
                next_order += 1
            slot["sort_order"] = next_order
            seen.add(next_order)
            next_order += 1
    return slots


def _window_grammar_422(message: str) -> HTTPException:
    """Route a window/anchor grammar failure at its field (422 shape)."""
    lowered = message.lower()
    if "window_start" in lowered:
        return invalid("window_start", message)
    if "window_end" in lowered:
        return invalid("window_end", message)
    if "anchor" in lowered:
        return invalid("anchor_marker", message)
    return invalid([], message)


def _validate_playlist_entry(data: Any) -> PlaylistFile:
    """Structural (pairing/cap) + window/anchor grammar before any rename.

    The file model leaves windows/anchor unvalidated, so writes run
    ``_playlist_from_file`` + ``parse_window`` here; every failure is
    422 ``[{loc, msg}]``, never 500.
    """
    try:
        entry = PlaylistFile.model_validate(data)
    except ValidationError as exc:
        detail: list[dict[str, Any]] = []
        for err in exc.errors():
            loc: list[str | int] = list(err["loc"])
            if not loc:
                loc = ["max_cycles"] if "max_cycles" in str(err["msg"]) else ["items"]
            detail.append(
                {"loc": ["body", *loc], "msg": err["msg"], "type": err["type"]}
            )
        raise HTTPException(status_code=422, detail=detail) from exc
    try:
        playlist = _playlist_from_file(entry)
        parse_window(playlist)
    except ConfigError as exc:
        raise _window_grammar_422(str(exc)) from exc
    except ValueError as exc:
        raise _window_grammar_422(str(exc)) from exc
    return entry


def _mutate_playlists(
    request: Request, mutate: Callable[[list[Any]], int | None]
) -> tuple[list[Any], Any | None]:
    """Locked playlist write: read + mutate + validate + rename.

    ``mutate`` edits the raw entries and returns the affected index
    (``None`` for pure removals, which only shrink a validated row).
    The affected candidate gets structural + window validation, then the
    full file gets ``ConfigFile`` + ``Settings`` validation, then the
    rename. Returns the stored entries and the affected row (if any).
    """
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        current: Any = raw.get("playlists", [])
        if not isinstance(current, list):
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"{config_path}: playlists must be a list"),
            )
        entries: list[Any] = cast(list[Any], current)
        affected = mutate(entries)
        if affected is not None:
            _validate_playlist_entry(entries[affected])
        merged = dict(raw)
        merged["playlists"] = entries
        try:
            cfg = ConfigFile.model_validate(merged)
        except ValidationError as exc:
            raise _mapped_422(exc, "playlists", ("playlists",)) from exc
        try:
            _settings_from_config(cfg)
        except ValueError as exc:
            raise invalid("playlists", str(exc)) from exc
        _write_config_json(config_path, merged)
        _publish_config_update(request)
        stored = entries[affected] if affected is not None else None
        return entries, stored


def _find_playlist_index(entries: Sequence[Any], playlist_id: str) -> int | None:
    """Row index for an exact id match (legacy rows compare as-is)."""
    for index, row in enumerate(entries):
        if isinstance(row, dict) and _as_dict(row).get("id") == playlist_id:
            return index
    return None


def _as_dict(row: Any) -> dict[str, Any]:
    """Treat a raw JSON row as a string-keyed dict (validated downstream)."""
    return cast(dict[str, Any], row)


def _row_items(row: dict[str, Any], playlist_id: str) -> list[Any]:
    """Raw items list of one row; corrupt shapes are 422 (never 500)."""
    raw: Any = row.get("items", [])
    if not isinstance(raw, list):
        raise invalid("items", f"playlist {playlist_id!r} items are corrupt")
    return cast(list[Any], raw)


def _slot_sort_order(item: Any) -> Any:
    """One slot's ``sort_order`` (non-dict slots never match an order)."""
    if not isinstance(item, dict):
        return None
    return _as_dict(item).get("sort_order")


@public_playlist_router.get("/api/playlists")
def list_playlists(request: Request) -> list[dict[str, Any]]:
    """Serve every playlist in file order (legacy rows as-is, no windowing)."""
    cfg = load_config_file(_config_path(request))
    return [entry.model_dump(mode="json") for entry in cfg.playlists]


@public_playlist_router.get("/api/playlists/preview")
def preview_playlists(
    request: Request,
    moment: Annotated[datetime, AfterValidator(_require_tz_aware_moment)],
) -> dict[str, Any]:
    """Resolve one moment's stage plus active ids (window testing).

    Registered before ``{id}``: ``preview`` matches the id
    grammar, so order decides. Schedule/config failures map like
    ``prayer-day`` (unresolvable → 404, invalid config → 503) by
    letting those errors reach the shared handlers.
    """
    engine = getattr(request.app.state, "engine", None)
    settings_repo = getattr(request.app.state, "settings_repo", None)
    playlist_repo = getattr(request.app.state, "playlist_repo", None)
    if engine is None or settings_repo is None:
        raise HTTPException(status_code=503, detail="config: unavailable")
    settings = settings_repo.load()
    playlists: list[Any] = playlist_repo.list() if playlist_repo is not None else []
    result = engine.resolve_day(moment.date(), settings.zone, moment)
    event = engine.next_event(moment)
    occupant = resolve_stage(moment, result.day, settings, event, playlists)
    return {
        "stage": stage_id(occupant),
        "active_playlists": [item.id for item in playlists if item.active],
    }


@public_playlist_router.get("/api/playlists/{id}")
def get_playlist(request: Request, id: str) -> dict[str, Any]:
    """Serve one playlist by id; unknown ids are 404 (reads skip grammar)."""
    cfg = load_config_file(_config_path(request))
    for entry in cfg.playlists:
        if entry.id == id:
            return entry.model_dump(mode="json")
    raise HTTPException(status_code=404, detail=f"unknown playlist id: {id!r}")


@admin_router.post("/api/playlists", status_code=201)
def create_playlist(request: Request, body: PlaylistCreate) -> dict[str, Any]:
    """Create a playlist (duplicate ids conflict; grammar enforced)."""
    pid = _check_playlist_id(body.id)
    title = _stripped("title", body.title)
    slots = [
        {
            **_check_item_slot(item.image_path, item.duration_s),
            "sort_order": item.sort_order,
        }
        for item in body.items
    ]
    data: dict[str, Any] = {
        "id": pid,
        "title": title,
        "active": body.active,
        "window_start": (
            body.window_start.strip() if body.window_start is not None else None
        ),
        "window_end": body.window_end.strip() if body.window_end is not None else None,
        "anchor_marker": (
            body.anchor_marker.strip() if body.anchor_marker is not None else None
        ),
        "anchor_start_offset_min": body.anchor_start_offset_min,
        "anchor_stop_offset_min": body.anchor_stop_offset_min,
        "cycle_mode": body.cycle_mode,
        "max_cycles": body.max_cycles,
        "items": _assign_create_orders(slots),
    }

    def _insert(entries: list[Any]) -> int:
        if _find_playlist_index(entries, pid) is not None:
            raise HTTPException(
                status_code=409, detail=f"duplicate playlist id: {pid!r}"
            )
        entries.append(data)
        return len(entries) - 1

    _, stored = _mutate_playlists(request, _insert)
    return cast(dict[str, Any], stored)


@admin_router.patch("/api/playlists/{id}")
def patch_playlist(request: Request, id: str, patch: PlaylistPatch) -> dict[str, Any]:
    """Partial playlist merge (omitted = unchanged, null clears windows/anchor).

    The ``id`` and ``items`` keys never reach here: the body model fails
    them closed (422) so renames and order-bypassing edits cannot land.
    """
    _reject_explicit_nulls(
        patch, {"window_start", "window_end", "anchor_marker", "max_cycles"}
    )
    changes: dict[str, Any] = {}
    if "title" in patch.model_fields_set and patch.title is not None:
        changes["title"] = _stripped("title", patch.title)
    for key in (
        "active",
        "anchor_start_offset_min",
        "anchor_stop_offset_min",
        "cycle_mode",
        "max_cycles",
    ):
        if key in patch.model_fields_set:
            changes[key] = getattr(patch, key)
    for key in ("window_start", "window_end", "anchor_marker"):
        if key in patch.model_fields_set:
            value = getattr(patch, key)
            changes[key] = value.strip() if isinstance(value, str) else None

    def _merge(entries: list[Any]) -> int:
        index = _find_playlist_index(entries, id)
        if index is None:
            raise HTTPException(status_code=404, detail=f"unknown playlist id: {id!r}")
        row = _as_dict(entries[index])
        merged: dict[str, Any] = dict(row)
        merged.update(changes)
        entries[index] = merged
        return index

    _, stored = _mutate_playlists(request, _merge)
    return cast(dict[str, Any], stored)


@admin_router.delete("/api/playlists/{id}")
def delete_playlist(request: Request, id: str) -> dict[str, bool]:
    """Delete a playlist by id; unknown ids are 404."""

    def _remove(entries: list[Any]) -> None:
        index = _find_playlist_index(entries, id)
        if index is None:
            raise HTTPException(status_code=404, detail=f"unknown playlist id: {id!r}")
        del entries[index]

    _mutate_playlists(request, _remove)
    return {"ok": True}


@admin_router.post("/api/playlists/{id}/items", status_code=201)
def create_playlist_item(
    request: Request, id: str, body: PlaylistItemCreate
) -> dict[str, Any]:
    """Append one item (duplicate ``sort_order`` conflicts, missing auto-assigns).

    Auto-assign is ``max(existing)+1`` (empty → ``0``): gaps persist and
    freed slots are never backfilled.
    """
    slot = _check_item_slot(body.image_path, body.duration_s)
    wanted = body.sort_order

    def _insert(entries: list[Any]) -> int:
        index = _find_playlist_index(entries, id)
        if index is None:
            raise HTTPException(status_code=404, detail=f"unknown playlist id: {id!r}")
        row = _as_dict(entries[index])
        current = _row_items(row, id)
        orders: list[Any] = [_slot_sort_order(item) for item in current]
        if wanted is None:
            numeric = [
                order
                for order in orders
                if isinstance(order, int) and not isinstance(order, bool)
            ]
            order = (max(numeric) + 1) if numeric else 0
        else:
            if wanted in orders:
                raise HTTPException(
                    status_code=409,
                    detail=(f"duplicate sort_order {wanted} in playlist {id!r}"),
                )
            order = wanted
        created = {**slot, "sort_order": order}
        updated: dict[str, Any] = dict(row)
        updated["items"] = [*current, created]
        entries[index] = updated
        return index

    _, stored = _mutate_playlists(request, _insert)
    items: Any = cast(dict[str, Any], stored)["items"]
    return cast(dict[str, Any], items[-1])


@admin_router.delete("/api/playlists/{id}/items/{sort_order}")
def delete_playlist_item(request: Request, id: str, sort_order: int) -> dict[str, bool]:
    """Delete one item by its ``sort_order`` value (ambiguous → 409 hand-fix)."""

    def _remove(entries: list[Any]) -> None:
        index = _find_playlist_index(entries, id)
        if index is None:
            raise HTTPException(status_code=404, detail=f"unknown playlist id: {id!r}")
        row = _as_dict(entries[index])
        current = _row_items(row, id)
        matches: list[Any] = [
            item for item in current if _slot_sort_order(item) == sort_order
        ]
        if not matches:
            raise HTTPException(
                status_code=404,
                detail=(f"unknown sort_order {sort_order} in playlist {id!r}"),
            )
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"ambiguous sort_order {sort_order}: {len(matches)} items "
                    f"share it in playlist {id!r} — hand-fix the "
                    "config file until deduped"
                ),
            )
        doomed = matches[0]
        updated: dict[str, Any] = dict(row)
        updated["items"] = [item for item in current if item is not doomed]
        entries[index] = updated

    _mutate_playlists(request, _remove)
    return {"ok": True}


# --- Displays: collection GET + PUT/PATCH/DELETE (spec §3). ---

public_display_router = APIRouter()
"""Public display reads (spec §1): the collection stays open, no audit."""


class DisplayPut(_Partial):
    """Display full-replace body: omitted resets to file-model defaults."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    language: Language = "en"
    theme: DisplayTheme = Field(default_factory=DisplayTheme)
    dim_minutes_override: Annotated[int, Field(ge=5, le=60)] | None = None
    carousel_enabled: bool = True


class DisplayPatch(_Partial):
    """Display partial: omitted = unchanged, theme knobs merge per knob."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    language: Language | None = None
    theme: DisplayTheme | None = None
    dim_minutes_override: Annotated[int, Field(ge=5, le=60)] | None = None
    carousel_enabled: bool | None = None


def _validate_display_entry(data: Any) -> Display:
    """Structural display check before any rename (422, body-relative locs)."""
    try:
        return Display.model_validate(data)
    except ValidationError as exc:
        detail: list[dict[str, Any]] = []
        for err in exc.errors():
            loc: list[str | int] = list(err["loc"])
            if not loc:
                loc = ["displays"]
            detail.append(
                {"loc": ["body", *loc], "msg": err["msg"], "type": err["type"]}
            )
        raise HTTPException(status_code=422, detail=detail) from exc


def _mutate_displays(
    request: Request, mutate: Callable[[dict[str, Any]], str | None]
) -> tuple[dict[str, Any], Any | None]:
    """Locked display write: read + mutate + validate + rename.

    ``mutate`` edits the raw map and returns the affected id (``None``
    for pure removals, which only shrink a validated row). The affected
    candidate gets structural validation, then the full file gets
    ``ConfigFile`` + ``Settings`` validation, then the rename. Returns
    the stored map and the affected row (if any).
    """
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        current: Any = raw.get("displays", {})
        if not isinstance(current, dict):
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(
                    f"{config_path}: displays must be an object"
                ),
            )
        displays: dict[str, Any] = cast(dict[str, Any], current)
        affected = mutate(displays)
        if affected is not None:
            _validate_display_entry(displays[affected])
        merged = dict(raw)
        merged["displays"] = displays
        try:
            cfg = ConfigFile.model_validate(merged)
        except ValidationError as exc:
            raise _mapped_422(exc, "displays", ("displays",)) from exc
        try:
            _settings_from_config(cfg)
        except ValueError as exc:
            raise invalid("displays", str(exc)) from exc
        _write_config_json(config_path, merged)
        _publish_config_update(request)
        stored = displays[affected] if affected is not None else None
        return displays, stored


def _display_replace_body(body: DisplayPut) -> dict[str, Any]:
    """Full-replace entry: stripped name, whole theme dump, validated ranges."""
    return {
        "name": _stripped("name", body.name) if body.name is not None else None,
        "language": body.language,
        "theme": body.theme.model_dump(),
        "dim_minutes_override": body.dim_minutes_override,
        "carousel_enabled": body.carousel_enabled,
    }


@public_display_router.get("/api/displays")
def list_displays(request: Request) -> dict[str, Any]:
    """Serve every display in file order (legacy rows as-is)."""
    cfg = load_config_file(_config_path(request))
    return {key: entry.model_dump(mode="json") for key, entry in cfg.displays.items()}


@admin_router.put("/api/displays/{id}")
def put_display(
    request: Request, response: Response, id: str, body: DisplayPut
) -> dict[str, Any]:
    """Upsert a display by full replace (201 create, 200 replace).

    Missing fields reset to defaults; ``{}`` resets to all-defaults.
    A new id faces the shared grammar (422); replacing a legacy
    out-of-grammar row keeps its key.
    """
    replacement = _display_replace_body(body)
    existed_holder: list[bool] = []

    def _upsert(displays: dict[str, Any]) -> str:
        existed_holder.append(id in displays)
        if not existed_holder[0]:
            # Shared id grammar, new values only (legacy replaces keep keys).
            _check_playlist_id(id)
        displays[id] = replacement
        return id

    _, stored = _mutate_displays(request, _upsert)
    response.status_code = 200 if existed_holder[0] else 201
    return cast(dict[str, Any], stored)


@admin_router.patch("/api/displays/{id}")
def patch_display(request: Request, id: str, patch: DisplayPatch) -> dict[str, Any]:
    """Partial display merge (omitted = unchanged, theme knobs per knob).

    Explicit null clears nullable ``name``/``dim_minutes_override`` and
    inherits that theme knob; null on ``language``/``carousel_enabled``
    or the theme object itself is 422 (non-nullable).
    """
    _reject_explicit_nulls(patch, {"name", "dim_minutes_override"})
    changes: dict[str, Any] = {}
    if "name" in patch.model_fields_set:
        changes["name"] = (
            _stripped("name", patch.name) if patch.name is not None else None
        )
    for key in ("language", "dim_minutes_override", "carousel_enabled"):
        if key in patch.model_fields_set:
            changes[key] = getattr(patch, key)
    knob_changes: dict[str, Any] | None = None
    if "theme" in patch.model_fields_set and patch.theme is not None:
        knob_changes = {
            key: getattr(patch.theme, key) for key in patch.theme.model_fields_set
        }

    def _merge(displays: dict[str, Any]) -> str:
        if id not in displays:
            raise HTTPException(status_code=404, detail=f"unknown display id: {id!r}")
        row = displays[id]
        if not isinstance(row, dict):
            raise invalid([], f"display {id!r} entry is corrupt")
        merged: dict[str, Any] = dict(cast(dict[str, Any], row))
        merged.update(changes)
        if knob_changes is not None:
            current_theme = merged.get("theme")
            base: dict[str, Any] = (
                dict(cast(dict[str, Any], current_theme))
                if isinstance(current_theme, dict)
                else {}
            )
            base.update(knob_changes)
            merged["theme"] = base
        displays[id] = merged
        return id

    _, stored = _mutate_displays(request, _merge)
    return cast(dict[str, Any], stored)


@admin_router.delete("/api/displays/{id}")
def delete_display(request: Request, id: str) -> dict[str, bool]:
    """Delete a display by id; unknown ids are 404."""

    def _remove(displays: dict[str, Any]) -> None:
        if id not in displays:
            raise HTTPException(status_code=404, detail=f"unknown display id: {id!r}")
        del displays[id]

    _mutate_displays(request, _remove)
    return {"ok": True}


# --- Manual-days: PUT/DELETE/validate (spec §3; GET lives above, public). ---

_MANUAL_MARKERS = (
    "imsak",
    "fajr",
    "syuruq",
    "dhuha",
    "dhuhr",
    "asr",
    "maghrib",
    "isha",
)
"""The eight day-local markers a pin may correct (mirrors file_models)."""


class ManualDayPut(_Partial):
    """Single-pin PUT body: date optional (path authoritative), markers optional."""

    date: _date | None = None
    imsak: TimeHHMM | None = None
    fajr: TimeHHMM | None = None
    syuruq: TimeHHMM | None = None
    dhuha: TimeHHMM | None = None
    dhuhr: TimeHHMM | None = None
    asr: TimeHHMM | None = None
    maghrib: TimeHHMM | None = None
    isha: TimeHHMM | None = None


def _manual_schedule_section(raw: dict[str, Any]) -> dict[str, Any]:
    """Raw schedule section (missing/non-object routes as empty inline)."""
    section = raw.get("schedule")
    return cast(dict[str, Any], section) if isinstance(section, dict) else {}


def _manual_pins_path(config_path: Path, section: Mapping[str, Any]) -> Path | None:
    """Resolved pins path when the ref is set, else ``None`` for inline.

    Blank refs route inline (mirrors ``manual_days_file_for_config``);
    the ref string itself only changes via ``PATCH schedule``.
    """
    ref = section.get("manual_days_file")
    if not isinstance(ref, str) or not ref.strip():
        return None
    return resolve_pins_path(config_path, ref)


def _reject_coexisting_inline(section: Mapping[str, Any]) -> None:
    """Inline pins plus a set ref stay exclusive (writes are 422)."""
    current = section.get("manual_days")
    if isinstance(current, list) and current:
        raise invalid(
            "manual_days_file",
            "schedule.manual_days and schedule.manual_days_file are exclusive",
        )


def _pins_file_detail(ref: str, exc: Exception) -> str:
    """Scrubbed pins-file error that still names the configured ref.

    The scrubber hides server absolute paths (the resolved pins path
    included), so the operator-facing detail prefixes the hand-fixable
    ref (``pins.json``, never an absolute path) to stay actionable.
    """
    return f"manual_days_file {ref}: {_scrub_config_error(str(exc))}"


def _manual_day_422(exc: ValidationError) -> HTTPException:
    """Map one pin's structural failures to 422 [{loc, msg}] (never 500)."""
    detail: list[dict[str, Any]] = []
    for err in exc.errors():
        loc: list[str | int] = list(err["loc"])
        if not loc:
            loc = ["date"]
        detail.append({"loc": ["body", *loc], "msg": err["msg"], "type": err["type"]})
    return HTTPException(status_code=422, detail=detail)


def _pins_completion_422(
    pins: Sequence[ManualDay], exc: ConfigError, *, single: bool
) -> HTTPException:
    """Map buffer-completion failures to 422 (trap message for partials).

    A partial pin with no buffer row for its date+zone names the trap
    (``no buffer row for <date> — sync first or send a full-day pin``);
    ordering failures keep their scrubbed detail. Single-pin PUT locates
    the date; bare-array validates locate the failing index.
    """
    text = _scrub_config_error(str(exc))
    day: str | None = None
    if "missing markers" in text:
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        day = match.group(0) if match else None
        text = (
            f"no buffer row for {day or 'that date'} — "
            "sync first or send a full-day pin"
        )
    loc: list[str | int] = ["body", "date"] if single else ["body"]
    if not single and day is not None:
        for index, pin in enumerate(pins):
            if pin.date.isoformat() == day:
                loc = ["body", index]
                break
    return HTTPException(
        status_code=422,
        detail=[{"loc": loc, "msg": text, "type": "value_error"}],
    )


def _check_completion(
    request: Request, pins: Sequence[ManualDay], zone: str, *, single: bool
) -> None:
    """Run buffer-completion over candidate pins (structural already held)."""
    repo = getattr(request.app.state, "prayer_repo", None)
    if not isinstance(repo, FilePrayerRepo):
        return
    try:
        repo.validate_pins(pins, zone)
    except ConfigError as exc:
        raise _pins_completion_422(pins, exc, single=single) from exc


@admin_router.put("/api/config/manual-days/{date}")
def put_manual_day(request: Request, date: _date, body: ManualDayPut) -> dict[str, Any]:
    """Upsert one pin by date (replace on duplicate, 200 — never 409).

    The path date is authoritative: a body date must match it or 422.
    Writes route by the pins ref (file) or inline; a set-but-absent ref
    is created here (parents mkdir'd, bare array atomically written).
    """
    if body.date is not None and body.date != date:
        raise invalid(
            "date",
            f"body date {body.date.isoformat()} does not match "
            f"path date {date.isoformat()}",
        )
    try:
        candidate = ManualDay.model_validate(
            {
                "date": date.isoformat(),
                **{
                    key: getattr(body, key)
                    for key in _MANUAL_MARKERS
                    if getattr(body, key) is not None
                },
            }
        )
    except ValidationError as exc:
        raise _manual_day_422(exc) from exc
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        section = _manual_schedule_section(raw)
        pins_path = _manual_pins_path(config_path, section)
        if pins_path is not None:
            _reject_coexisting_inline(section)
            ref = section.get("manual_days_file")
            try:
                pins = load_manual_days_file(pins_path)
            except ConfigError as exc:
                if pins_path.exists():
                    raise HTTPException(
                        status_code=422,
                        detail=[
                            {
                                "loc": ["body", "manual_days_file"],
                                "msg": (
                                    _pins_file_detail(ref, exc)
                                    if isinstance(ref, str)
                                    else _scrub_config_error(str(exc))
                                ),
                                "type": "value_error",
                            }
                        ],
                    ) from exc
                pins = []
            new_pins = [pin for pin in pins if pin.date != date]
            new_pins.append(candidate)
            try:
                ensure_unique_manual_dates(new_pins)
            except ValueError as exc:
                raise invalid("date", str(exc)) from exc
            _check_completion(request, new_pins, _effective_zone(section), single=True)
            try:
                pins_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json_list(
                    pins_path, [pin.model_dump(mode="json") for pin in new_pins]
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=_scrub_config_error(
                        f"{pins_path}: cannot write manual days file: {exc}"
                    ),
                ) from exc
            _publish_config_update(request)
        else:
            current = section.get("manual_days", [])
            if "manual_days" in section and not isinstance(current, list):
                raise HTTPException(
                    status_code=503,
                    detail=_scrub_config_error(
                        f"{config_path}: schedule.manual_days must be a list"
                    ),
                )
            rows = cast(list[Any], current)
            kept: list[Any] = [
                entry
                for entry in rows
                if not (
                    isinstance(entry, dict)
                    and _as_dict(entry).get("date") == date.isoformat()
                )
            ]
            merged_section = dict(section)
            merged_section["manual_days"] = [
                *kept,
                candidate.model_dump(mode="json"),
            ]
            merged = dict(raw)
            merged["schedule"] = merged_section
            cfg = _validate_merged(merged, "schedule", ("manual_days",))
            _check_completion(
                request,
                list(cfg.schedule.manual_days),
                cfg.schedule.effective_zone,
                single=True,
            )
            _write_config_json(config_path, merged)
            _publish_config_update(request)
    return candidate.model_dump(mode="json")


@admin_router.delete("/api/config/manual-days/{date}")
def delete_manual_day(request: Request, date: _date) -> dict[str, bool]:
    """Delete one pin by date; unknown dates are 404.

    A set-but-absent pins ref is 503 like GET — there is no file to
    delete from. File-mode DELETE shrinks the pins file; the orphaned
    file on a file→inline migration is never auto-deleted.
    """
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        section = _manual_schedule_section(raw)
        pins_path = _manual_pins_path(config_path, section)
        if pins_path is not None:
            _reject_coexisting_inline(section)
            ref = section.get("manual_days_file")
            try:
                pins = load_manual_days_file(pins_path)
            except ConfigError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        _pins_file_detail(ref, exc)
                        if isinstance(ref, str)
                        else _scrub_config_error(str(exc))
                    ),
                ) from exc
            kept = [pin for pin in pins if pin.date != date]
            if len(kept) == len(pins):
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown manual day: {date.isoformat()!r}",
                )
            try:
                _atomic_write_json_list(
                    pins_path, [pin.model_dump(mode="json") for pin in kept]
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=_scrub_config_error(
                        f"{pins_path}: cannot write manual days file: {exc}"
                    ),
                ) from exc
            _publish_config_update(request)
        else:
            current = section.get("manual_days", [])
            if not isinstance(current, list):
                raise HTTPException(
                    status_code=503,
                    detail=_scrub_config_error(
                        f"{config_path}: schedule.manual_days must be a list"
                    ),
                )
            rows = cast(list[Any], current)
            kept_raw: list[Any] = [
                entry
                for entry in rows
                if not (
                    isinstance(entry, dict)
                    and _as_dict(entry).get("date") == date.isoformat()
                )
            ]
            if len(kept_raw) == len(rows):
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown manual day: {date.isoformat()!r}",
                )
            merged_section = dict(section)
            merged_section["manual_days"] = kept_raw
            merged = dict(raw)
            merged["schedule"] = merged_section
            _validate_merged(merged, "schedule", ("manual_days",))
            _write_config_json(config_path, merged)
            _publish_config_update(request)
    return {"ok": True}


@admin_router.post("/api/config/manual-days/validate")
def validate_manual_days(request: Request, pins: list[ManualDay]) -> dict[str, bool]:
    """Dry-run bare-array validation (structural + buffer-completion).

    The same two tiers as PUT — including the pre-sync partial trap —
    gated like writes, writing nothing.
    """
    try:
        ensure_unique_manual_dates(pins)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body"], "msg": str(exc), "type": "value_error"}],
        ) from exc
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        zone = _effective_zone(_manual_schedule_section(raw))
        _check_completion(request, pins, zone, single=False)
    return {"ok": True}


# --- Media: public list + gated upload/delete (spec §4). ---

public_media_router = APIRouter()
"""Public media reads (spec §1): the file list stays open, no audit."""


def _media_dir(request: Request) -> Path:
    """Media root for this app; state set by ``create_app`` (fallback static)."""
    media = getattr(request.app.state, "media_dir", None)
    if media is None:
        return Path(__file__).resolve().parent.parent / "static" / "uploads"
    return Path(media)


@public_media_router.get("/api/media")
def list_media(request: Request) -> list[dict[str, Any]]:
    """Serve every media file as media-relative ``[{path, size_bytes}]``."""
    return list_media_files(_media_dir(request))


@admin_router.post("/api/media")
def upload_media(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()],
) -> dict[str, str]:
    """Store one upload atomically; ``kind`` routes root vs ``playlists/``.

    ``adhan`` MP3s land at the media root, ``image`` files under
    ``playlists/`` (created on write). Basenames flatten (no subdirs v1)
    and overwrite is allowed: a new relpath answers ``201``, an existing
    one ``200`` with ``{path}``. Oversize aborts the bounded streaming
    read with ``413`` before any decode; every other content failure is
    ``422``. Sync ``def`` (threadpool): Pillow re-encode + fsync never
    block the event loop. The final existence-probe + rename holds the
    shared write lock so backup export/restore cannot interleave a torn
    manifest (validation + decode stay outside the lock).
    """
    try:
        basename = sanitize_upload_basename(file.filename)
    except ValueError as exc:
        raise invalid("file", str(exc)) from exc
    try:
        relpath = destination_for_kind(kind, basename)
    except ValueError as exc:
        raise invalid("kind", str(exc)) from exc
    cap = cap_for_kind(kind)
    claimed = request.headers.get("content-length")
    if claimed is not None:
        try:
            declared = int(claimed)
        except ValueError:
            declared = None
        if declared is not None and declared > cap + UPFRONT_SLACK_BYTES:
            raise HTTPException(status_code=413, detail=f"upload exceeds {cap} bytes")
    try:
        data = read_upload_bounded_sync(file, cap)
    except MediaTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"cannot read upload: {exc}"),
        ) from exc
    normalized_kind = kind.strip()
    try:
        if normalized_kind == "adhan":
            verify_audio_mp3(data, basename)
            payload = data
        else:
            payload = reencode_image(data, basename)
    except ValueError as exc:
        raise invalid("file", str(exc)) from exc
    dest = media_path_for_rel(_media_dir(request), relpath)
    lock = get_write_lock(request)
    with lock:
        try:
            existed = dest_is_file(dest)
            atomic_write_bytes(dest, payload)
        except ValueError as exc:
            raise invalid("file", str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"{dest}: cannot write media file: {exc}"),
            ) from exc
    response.status_code = 200 if existed else 201
    return {"path": relpath}


@admin_router.delete("/api/media/{path:path}")
def delete_media(request: Request, path: str) -> dict[str, bool]:
    """Delete one media file; unknown relpaths are 404.

    Unconditional by design: files referenced by playlist items or the
    adhan config delete fine (dangling refs stay permitted) — no
    per-delete reference scan ever runs. The existence-probe + unlink
    holds the shared write lock so backup export/restore cannot
    interleave a torn manifest.
    """
    try:
        rel = normalize_media_rel(path)
    except ValueError as exc:
        raise invalid("path", str(exc)) from exc
    dest = media_path_for_rel(_media_dir(request), rel)
    lock = get_write_lock(request)
    with lock:
        try:
            found = dest_is_file(dest)
        except ValueError as exc:
            raise invalid("path", str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"{dest}: cannot delete media file: {exc}"),
            ) from exc
        if not found:
            raise HTTPException(status_code=404, detail=f"unknown media path: {path!r}")
        try:
            dest.unlink()
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise HTTPException(
                    status_code=404, detail=f"unknown media path: {path!r}"
                ) from exc
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"{dest}: cannot delete media file: {exc}"),
            ) from exc
    return {"ok": True}


# --- Backup export/restore + logs (spec §4). ---

BACKUP_MEDIA_CAP_BYTES = 50 * 1024 * 1024
"""Uncompressed media cap for export sums and restore media totals.

Past it the export aborts with ``413`` before archiving and the
restore aborts with ``413`` before touching disk.
"""

BACKUP_UPLOAD_CAP_BYTES = 50 * 1024 * 1024
"""Bounded-streaming cap for the restore zip upload (``413`` past it)."""

_BACKUP_CHUNK_BYTES = 64 * 1024
"""Streaming quantum for restore uploads and export zip streaming."""

_BACKUP_CONFIG_NAME = "muhideen.json"
_BACKUP_PINS_NAME = "pins.json"
_BACKUP_BUFFER_NAME = "prayer_buffer.json"
_BACKUP_MANIFEST_NAME = "manifest.json"
_BACKUP_MEDIA_PREFIX = "media/"
"""Archive layout: config/pins/buffer/manifest at the root, media below."""

_BACKUP_NON_MEDIA_CAP_BYTES = 10 * 1024 * 1024
"""Sanity cap per non-media zip member (guards staging disk; 422 past it)."""

_RESTORE_STAGING_PREFIX = ".restore-"
_RESTORE_STAGING_SUFFIX = ".tmp"
"""Staging dirs read ``<config-dir>/.restore-*.tmp`` (same filesystem)."""

_LOGS_UNIT = "muhideen"
_LOGS_TIMEOUT_S = 5
"""``journalctl`` argv unit and timeout (timeout answers 501, like missing)."""


def _live_buffer_path(request: Request, config_path: Path) -> Path:
    """Live buffer file: the repo's path, else the config sibling default."""
    repo = getattr(request.app.state, "prayer_repo", None)
    if isinstance(repo, FilePrayerRepo):
        return repo.buffer_path
    return config_path.parent / "prayer_buffer.json"


def _export_manifest_entry(path: str, data: bytes) -> dict[str, Any]:
    """One manifest row: relative path, byte size, and SHA256 hex digest."""
    return {
        "path": path,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _iter_file_and_cleanup(path: str) -> Iterator[bytes]:
    """Stream a temp zip in chunks, unlinking it on every exit path."""
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_BACKUP_CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
    finally:
        with suppress(OSError):
            os.unlink(path)


@admin_router.get(
    "/api/backup/export",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Whole-installation zip (config + pins + buffer + manifest + media)."
            ),
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        401: {"description": "Missing or wrong admin token."},
        413: {
            "description": (
                "Uncompressed media exceeds the 50MB cap (summed before archiving)."
            ),
            "content": {
                "application/json": {
                    "example": {"detail": "backup media exceeds 52428800 bytes"}
                }
            },
        },
        503: {"description": "Admin writes disabled or broken config."},
    },
)
def export_backup(request: Request) -> StreamingResponse:
    """Download the installation as one zip (media streams, never buffered).

    Members are ``muhideen.json`` + the pins file when the ref is set +
    ``prayer_buffer.json`` when present (byte-copied as-is, even corrupt
    — backup is not validation) + ``manifest.json``
    (``{files:[{path,size_bytes,sha256}], exported_at}``) + media
    binaries under ``media/``. Every member path is relative, never
    absolute. Media sizes pre-sum before archiving (over the cap →
    ``413``); each file then streams chunk-by-chunk into its zip member
    (``O_NOFOLLOW`` open, never following symlinks) while its SHA256
    accumulates, so the manifest describes the bytes actually archived
    and RAM stays flat regardless of media totals. A running total
    aborts ``413`` mid-stream if concurrent growth pushes past the cap.
    Files vanishing mid-export are skipped (logged) rather than failing
    the backup with a 500. The config/pins/buffer snapshot holds the
    write lock only for the byte copies — the zip build runs outside
    the lock so exports never block config writes. Disk faults while
    building the archive are ``503`` like every other ``OSError`` path.
    """
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        raw = _read_raw_config(config_path)
        try:
            config_data = config_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"{config_path}: cannot read config: {exc}"),
            ) from exc
        pins_data: bytes | None = None
        pins_path = _manual_pins_path(config_path, _manual_schedule_section(raw))
        if pins_path is not None:
            try:
                pins_data = pins_path.read_bytes()
            except OSError as exc:
                ref = raw.get("schedule", {}).get("manual_days_file")
                raise HTTPException(
                    status_code=503,
                    detail=(
                        _pins_file_detail(ref, exc)
                        if isinstance(ref, str)
                        else _scrub_config_error(str(exc))
                    ),
                ) from exc
        buffer_data: bytes | None = None
        live_buffer = _live_buffer_path(request, config_path)
        if live_buffer.is_file():
            try:
                buffer_data = live_buffer.read_bytes()
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=_scrub_config_error(
                        f"{live_buffer}: cannot read prayer buffer: {exc}"
                    ),
                ) from exc
        media_root = _media_dir(request)
        entries = list_media_files(media_root)
        media_total = sum(entry["size_bytes"] for entry in entries)
        if media_total > BACKUP_MEDIA_CAP_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"backup media exceeds {BACKUP_MEDIA_CAP_BYTES} bytes",
            )
        rels = [entry["path"] for entry in entries]
    small_members: list[tuple[str, bytes]] = [(_BACKUP_CONFIG_NAME, config_data)]
    if pins_data is not None:
        small_members.append((_BACKUP_PINS_NAME, pins_data))
    if buffer_data is not None:
        small_members.append((_BACKUP_BUFFER_NAME, buffer_data))
    media_manifest: list[dict[str, Any]] = []
    media_streamed_total = 0
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="muhideen-backup-", suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in small_members:
                zf.writestr(path, data)
            for rel in rels:
                candidate = media_root / rel
                try:
                    raw_fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
                except OSError as exc:
                    logger.warning(
                        "backup export skipping %s (concurrent change: %s)", rel, exc
                    )
                    continue
                try:
                    with os.fdopen(raw_fd, "rb") as src:
                        digest = hashlib.sha256()
                        size = 0
                        with zf.open(f"{_BACKUP_MEDIA_PREFIX}{rel}", "w") as dest:
                            while True:
                                try:
                                    chunk = src.read(_BACKUP_CHUNK_BYTES)
                                except OSError as exc:
                                    raise HTTPException(
                                        status_code=503,
                                        detail=_scrub_config_error(
                                            f"backup export failed: {exc}"
                                        ),
                                    ) from exc
                                if not chunk:
                                    break
                                size += len(chunk)
                                media_streamed_total += len(chunk)
                                if media_streamed_total > BACKUP_MEDIA_CAP_BYTES:
                                    raise HTTPException(
                                        status_code=413,
                                        detail=(
                                            "backup media exceeds "
                                            f"{BACKUP_MEDIA_CAP_BYTES} bytes"
                                        ),
                                    )
                                digest.update(chunk)
                                try:
                                    dest.write(chunk)
                                except OSError as exc:
                                    raise HTTPException(
                                        status_code=503,
                                        detail=_scrub_config_error(
                                            f"backup export failed: {exc}"
                                        ),
                                    ) from exc
                except HTTPException:
                    raise
                except OSError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=_scrub_config_error(f"backup export failed: {exc}"),
                    ) from exc
                media_manifest.append(
                    {
                        "path": f"{_BACKUP_MEDIA_PREFIX}{rel}",
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            manifest = {
                "files": [
                    _export_manifest_entry(path, data) for path, data in small_members
                ]
                + media_manifest,
                "exported_at": datetime.now(UTC).isoformat(),
            }
            zf.writestr(
                _BACKUP_MANIFEST_NAME,
                (json.dumps(manifest, indent=2) + "\n").encode(),
            )
        size = os.path.getsize(tmp_name)
    except HTTPException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise
    except OSError as exc:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"backup export failed: {exc}"),
        ) from exc
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"muhideen-backup-{stamp}.zip"
    return StreamingResponse(
        _iter_file_and_cleanup(tmp_name),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
        },
    )


def _zip_relpath(raw: str) -> str:
    """Harden one zip member name to a relative relpath (zip-slip gate).

    Rejects empty names, backslash separators, absolute and
    drive-letter paths, and any ``..`` segment — before ``normpath``,
    so ``a/../b``-style collapses never smuggle an escape. Anything
    escaping the archive root after normalization is rejected too.
    """
    if not raw:
        raise invalid("file", "backup archive has an empty member name")
    if "\\" in raw:
        raise invalid("file", f"backup member must use '/' separators: {raw!r}")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) is not None:
        raise invalid("file", f"backup member must be relative: {raw!r}")
    if any(segment == ".." for segment in raw.split("/")):
        raise invalid("file", f"backup member escapes the archive root: {raw!r}")
    norm = posixpath.normpath(raw)
    if norm in ("", ".") or norm == ".." or norm.startswith(("../", "/")):
        raise invalid("file", f"backup member escapes the archive root: {raw!r}")
    return norm


def _cleanup_stale_staging(config_path: Path) -> None:
    """Remove previous ``.restore-*.tmp`` dirs (kept for forensics, now spent)."""
    for stale in sorted(config_path.parent.glob(f"{_RESTORE_STAGING_PREFIX}*")):
        if stale.name.endswith(_RESTORE_STAGING_SUFFIX) and stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)


def _read_restore_upload(request: Request, file: UploadFile) -> bytes:
    """Bounded streaming read of the restore zip (``413`` past the cap).

    Sync ``def`` compatible: reads the already-parsed multipart part via
    its file object in chunks, so the cap aborts at cap+1 without ever
    buffering the whole body first.
    """
    claimed = request.headers.get("content-length")
    if claimed is not None:
        try:
            declared = int(claimed)
        except ValueError:
            declared = None
        if (
            declared is not None
            and declared > BACKUP_UPLOAD_CAP_BYTES + UPFRONT_SLACK_BYTES
        ):
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds {BACKUP_UPLOAD_CAP_BYTES} bytes",
            )
    part = file.file
    try:
        part.seek(0)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=_scrub_config_error(f"cannot read upload: {exc}"),
        ) from exc
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = part.read(_BACKUP_CHUNK_BYTES)
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(f"cannot read upload: {exc}"),
            ) from exc
        if not chunk:
            break
        total += len(chunk)
        if total > BACKUP_UPLOAD_CAP_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds {BACKUP_UPLOAD_CAP_BYTES} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _harden_restore_members(
    zf: zipfile.ZipFile,
) -> tuple[bytes, bytes | None, bytes | None, bytes | None, list[tuple[str, int, str]]]:
    """Partition hardened members: config/pins/buffer/manifest + media list.

    Returns the small members' bytes plus ``[(member, file_size, rel)]``
    for media (extracted streaming later). Unknown top-level members,
    symlinks, encrypted entries, duplicates, oversize non-media members,
    and an over-cap media total all fail here — before live disk moves.
    Truncated/crafted members that fail to decode are 422, never 500.
    """

    def _read_member(name: str, rel: str) -> bytes:
        try:
            return zf.read(name)
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            RuntimeError,
            NotImplementedError,
            EOFError,
            zlib.error,
        ) as exc:
            raise invalid("file", f"backup member unreadable: {rel!r} ({exc})") from exc

    infos = zf.infolist()
    seen: set[str] = set()
    config_data: bytes | None = None
    pins_data: bytes | None = None
    buffer_data: bytes | None = None
    manifest_data: bytes | None = None
    media: list[tuple[str, int, str]] = []
    media_total = 0
    for info in infos:
        if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
            raise invalid("file", f"backup member is a symlink: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise invalid("file", f"backup member is encrypted: {info.filename!r}")
        rel = _zip_relpath(info.filename)
        if info.is_dir():
            if rel != _BACKUP_MEDIA_PREFIX.rstrip("/"):
                raise invalid("file", f"unknown backup member: {rel!r}")
            continue
        if rel in seen:
            raise invalid("file", f"duplicate backup member: {rel!r}")
        seen.add(rel)
        if rel == _BACKUP_CONFIG_NAME:
            if info.file_size > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
            config_data = _read_member(info.filename, rel)
            if len(config_data) > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
        elif rel == _BACKUP_PINS_NAME:
            if info.file_size > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
            pins_data = _read_member(info.filename, rel)
            if len(pins_data) > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
        elif rel == _BACKUP_BUFFER_NAME:
            if info.file_size > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
            buffer_data = _read_member(info.filename, rel)
            if len(buffer_data) > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
        elif rel == _BACKUP_MANIFEST_NAME:
            if info.file_size > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
            manifest_data = _read_member(info.filename, rel)
            if len(manifest_data) > _BACKUP_NON_MEDIA_CAP_BYTES:
                raise invalid("file", f"backup member too large: {rel!r}")
        elif rel.startswith(_BACKUP_MEDIA_PREFIX):
            sub = rel[len(_BACKUP_MEDIA_PREFIX) :]
            if not sub:
                raise invalid("file", f"backup member must name a file: {rel!r}")
            try:
                normalized = normalize_media_rel(sub)
            except ValueError as exc:
                raise invalid(rel, str(exc)) from exc
            media_total += info.file_size
            if media_total > BACKUP_MEDIA_CAP_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"backup media exceeds {BACKUP_MEDIA_CAP_BYTES} bytes",
                )
            media.append((info.filename, info.file_size, normalized))
        else:
            raise invalid("file", f"unknown backup member: {rel!r}")
    if config_data is None:
        raise invalid("file", "backup archive is missing muhideen.json")
    if manifest_data is not None:
        try:
            manifest = json.loads(manifest_data.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise invalid(
                _BACKUP_MANIFEST_NAME, f"manifest is not JSON: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise invalid(
                _BACKUP_MANIFEST_NAME, "manifest must be {files: [...], exported_at}"
            )
        manifest_obj = cast(dict[str, Any], manifest)
        if not isinstance(manifest_obj.get("files"), list):
            raise invalid(
                _BACKUP_MANIFEST_NAME, "manifest must be {files: [...], exported_at}"
            )
    return config_data, pins_data, buffer_data, manifest_data, media


def _validate_staged_config(config_data: bytes) -> ConfigFile:
    """Full-file ``ConfigFile`` + ``Settings`` validation of staged bytes."""
    try:
        text = config_data.decode("utf-8")
    except UnicodeError as exc:
        raise invalid(_BACKUP_CONFIG_NAME, f"config is not UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise invalid(_BACKUP_CONFIG_NAME, f"config is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise invalid(_BACKUP_CONFIG_NAME, "config root must be an object")
    try:
        cfg = ConfigFile.model_validate(data)
    except ValidationError as exc:
        detail: list[dict[str, Any]] = []
        for err in exc.errors():
            loc: list[str | int] = list(err["loc"])
            if not loc:
                loc = ["schedule"]
            detail.append(
                {"loc": ["body", *loc], "msg": err["msg"], "type": err["type"]}
            )
        raise HTTPException(status_code=422, detail=detail) from exc
    try:
        _settings_from_config(cfg)
    except ValueError as exc:
        raise invalid(_BACKUP_CONFIG_NAME, str(exc)) from exc
    return cfg


def _check_staged_completion(
    pins: Sequence[ManualDay], zone: str, buffer_path: Path
) -> None:
    """Completion-check staged pins against the staged-outcome buffer.

    Partial pins with no row for their date+zone fail with the same
    pre-sync trap as the manual-days endpoints (never 500).
    """
    try:
        FilePrayerRepo(buffer_path).validate_pins(pins, zone)
    except ConfigError as exc:
        text = _scrub_config_error(str(exc))
        if "missing markers" in text:
            match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            day = match.group(0) if match else "that date"
            text = f"no buffer row for {day} — sync first or send a full-day pin"
        raise invalid(_BACKUP_PINS_NAME, text) from exc


def _promote(staged: Path, live: Path, renamed: list[tuple[Path, Path | None]]) -> None:
    """Rename staged → live on the same filesystem (EXDEV falls back to copy).

    Records ``(live, backup-or-None)`` for rollback; the caller keeps
    pre-rename backups inside staging, which forensics keeps on failure.
    """
    backup = staged.parent / "backups" / f"{live.name}.{os.getpid()}.bak"
    prior: Path | None = None
    if live.is_file():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(live, backup)
        prior = backup
    try:
        os.replace(staged, live)
    except OSError as exc:
        if exc.errno != 18:  # EXDEV: staging and target share no filesystem
            raise
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged, live)
    renamed.append((live, prior))


def _rollback_restore(renamed: list[tuple[Path, Path | None]]) -> None:
    """Best-effort rollback of promoted files from staging backups."""
    for live, backup in reversed(renamed):
        try:
            if backup is not None:
                shutil.copyfile(backup, live)
            else:
                live.unlink(missing_ok=True)
        except OSError:
            logger.warning("restore rollback failed for %s; keeping staging", live)


def _apply_restore(
    request: Request, config_path: Path, staging: Path, payload: bytes
) -> None:
    """Validate-all then staged-atomic apply (ordered renames + media).

    Staged-atomic, not single-rename atomic: after the first rename the
    watcher may reload a mixed generation (new config + old pins/buffer)
    and publish one transient ``config-update`` before the next rename
    converges it — subscribers tolerate one mixed-state event. Buffer
    race: a scheduler ``save_day`` landing between validate-all and the
    buffer rename is accepted last-wins either way; the next sync heals.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise invalid("file", f"backup archive is not a valid zip: {exc}") from exc
    renamed: list[tuple[Path, Path | None]] = []
    try:
        with archive:
            config_data, pins_data, buffer_data, _, media = _harden_restore_members(
                archive
            )
            staged_config = staging / _BACKUP_CONFIG_NAME
            staged_config.write_bytes(config_data)
            cfg = _validate_staged_config(config_data)
            staged_pins = staging / _BACKUP_PINS_NAME
            if pins_data is not None:
                staged_pins.write_bytes(pins_data)
                try:
                    staged_pin_list = load_manual_days_file(staged_pins)
                except ConfigError as exc:
                    raise invalid(
                        _BACKUP_PINS_NAME, _scrub_config_error(str(exc))
                    ) from exc
            else:
                staged_pin_list = []
            staged_buffer = staging / _BACKUP_BUFFER_NAME
            if buffer_data is not None:
                staged_buffer.write_bytes(buffer_data)
                try:
                    FilePrayerRepo(staged_buffer).validate_buffer()
                except ConfigError as exc:
                    raise invalid(
                        _BACKUP_BUFFER_NAME, _scrub_config_error(str(exc))
                    ) from exc
            live_buffer = _live_buffer_path(request, config_path)
            if buffer_data is not None:
                completion_buffer = staged_buffer
            else:
                completion_buffer = live_buffer
            ref = cfg.schedule.manual_days_file
            if pins_data is not None and ref is None:
                raise invalid(
                    _BACKUP_PINS_NAME,
                    "backup pins without a manual_days_file ref in muhideen.json",
                )
            pins_to_check: Sequence[ManualDay]
            if pins_data is not None:
                pins_to_check = staged_pin_list
            elif cfg.schedule.manual_days:
                pins_to_check = list(cfg.schedule.manual_days)
            elif ref is not None:
                live_pins = resolve_pins_path(config_path, ref)
                if live_pins.is_file():
                    try:
                        pins_to_check = load_manual_days_file(live_pins)
                    except ConfigError as exc:
                        raise invalid(
                            _BACKUP_PINS_NAME, _scrub_config_error(str(exc))
                        ) from exc
                else:
                    pins_to_check = []
            else:
                pins_to_check = []
            if pins_to_check:
                _check_staged_completion(
                    pins_to_check, cfg.schedule.effective_zone, completion_buffer
                )
            media_bytes: list[tuple[str, bytes]] = []
            media_actual_total = 0
            for member_name, _, normalized in media:
                try:
                    opener = archive.open(member_name)
                except (
                    zipfile.BadZipFile,
                    zipfile.LargeZipFile,
                    RuntimeError,
                    NotImplementedError,
                    EOFError,
                    zlib.error,
                ) as exc:
                    raise invalid(
                        "file",
                        f"backup member unreadable: {member_name!r} ({exc})",
                    ) from exc
                with opener as src:
                    chunks: list[bytes] = []
                    while True:
                        try:
                            chunk = src.read(_BACKUP_CHUNK_BYTES)
                        except (
                            zipfile.BadZipFile,
                            zipfile.LargeZipFile,
                            RuntimeError,
                            NotImplementedError,
                            EOFError,
                            zlib.error,
                        ) as exc:
                            raise invalid(
                                "file",
                                f"backup member unreadable: {member_name!r} ({exc})",
                            ) from exc
                        if not chunk:
                            break
                        chunks.append(chunk)
                        media_actual_total += len(chunk)
                        if media_actual_total > BACKUP_MEDIA_CAP_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    "backup media exceeds "
                                    f"{BACKUP_MEDIA_CAP_BYTES} bytes"
                                ),
                            )
                media_bytes.append((normalized, b"".join(chunks)))
            _promote(staged_config, config_path, renamed)
            if pins_data is not None and ref is not None:
                live_pins = resolve_pins_path(config_path, ref)
                live_pins.parent.mkdir(parents=True, exist_ok=True)
                _promote(staged_pins, live_pins, renamed)
            if buffer_data is not None:
                live_buffer.parent.mkdir(parents=True, exist_ok=True)
                _promote(staged_buffer, live_buffer, renamed)
            media_root = _media_dir(request)
            for normalized, data in sorted(media_bytes):
                dest = media_path_for_rel(media_root, normalized)
                backup = (
                    staging / "backups" / "media" / f"{normalized}.{os.getpid()}.bak"
                )
                prior: Path | None = None
                try:
                    already = dest_is_file(dest)
                except ValueError as exc:
                    raise invalid(
                        f"{_BACKUP_MEDIA_PREFIX}{normalized}", str(exc)
                    ) from exc
                if already:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(dest, backup)
                    prior = backup
                try:
                    atomic_write_bytes(dest, data)
                except ValueError as exc:
                    raise invalid(
                        f"{_BACKUP_MEDIA_PREFIX}{normalized}", str(exc)
                    ) from exc
                renamed.append((dest, prior))
            _publish_config_update(request)
    except (HTTPException, OSError):
        _rollback_restore(renamed)
        raise
    except Exception:
        _rollback_restore(renamed)
        logger.exception("restore failed unexpectedly; rolled back")
        raise
    # Success-only staging removal happens in the caller (failures keep
    # the dir for forensics until the next restore cleans it).


@admin_router.post(
    "/api/backup/restore",
    responses={
        200: {
            "description": "Restore applied (staged-atomic, ordered renames).",
            "content": {"application/json": {"example": {"ok": True}}},
        },
        401: {"description": "Missing or wrong admin token."},
        413: {
            "description": "Zip upload or media total exceeds the 50MB cap.",
            "content": {
                "application/json": {
                    "example": {"detail": "upload exceeds 52428800 bytes"}
                }
            },
        },
        422: {
            "description": (
                "Zip-slip member or validate-all failure (config/pins/buffer/media)."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "pins.json"],
                                "msg": "pins.json: invalid JSON",
                                "type": "value_error",
                            }
                        ]
                    }
                }
            },
        },
        503: {"description": "Admin writes disabled or a rename hit disk failure."},
    },
)
def restore_backup(
    request: Request, file: Annotated[UploadFile, File()]
) -> dict[str, bool]:
    """Replace the installation from a multipart backup zip (staged-atomic).

    The upload streams bounded (``≤50MB`` → ``413``), then zip-slip
    hardening (absolute/``..``/symlink/drive-letter rejected) and
    validate-all (config, pins-file semantics, buffer schema, staged
    pins completion against the STAGED buffer, media relpaths + total
    cap) run BEFORE live disk moves. Apply uses a same-filesystem
    staging dir with ordered renames (config → pins → buffer) plus
    additive per-file tmp+rename media (orphans persist, never
    deleted); pre-rename backups roll back on failure, staging is
    removed on success and kept on failure until the next restore.
    """
    payload = _read_restore_upload(request, file)
    lock = get_write_lock(request)
    config_path = _config_path(request)
    with lock:
        _cleanup_stale_staging(config_path)
        try:
            staging = Path(
                tempfile.mkdtemp(
                    dir=str(config_path.parent),
                    prefix=_RESTORE_STAGING_PREFIX,
                    suffix=_RESTORE_STAGING_SUFFIX,
                )
            )
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(
                    f"{config_path}: cannot apply restore: {exc}"
                ),
            ) from exc
        try:
            _apply_restore(request, config_path, staging, payload)
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=_scrub_config_error(
                    f"{config_path}: cannot apply restore: {exc}"
                ),
            ) from exc
        shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True}


@admin_router.get(
    "/api/logs",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Newest-last journal lines as text/plain.",
            "content": {"text/plain": {"example": "Oct 08 12:00:01 muhideen: tick\n"}},
        },
        401: {"description": "Missing or wrong admin token."},
        422: {
            "description": "``lines`` is not an int in 1-1000.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["query", "lines"],
                                "msg": "Input should be greater than or equal to 1",
                                "type": "greater_than_equal",
                            }
                        ]
                    }
                }
            },
        },
        501: {
            "description": "journald unavailable (compose/dev) or the unit is missing.",
            "content": {
                "application/json": {
                    "example": {"detail": "logs unavailable: journald is not available"}
                }
            },
        },
        503: {"description": "Admin writes disabled."},
    },
)
def read_logs(
    request: Request, lines: Annotated[int, Query(ge=1, le=1000)] = 200
) -> PlainTextResponse:
    """Tail the service journal newest-last (argv-only ``journalctl``).

    Runs ``journalctl -u muhideen --no-pager -n <lines>`` with no shell
    and a 5s timeout; timeouts, a missing binary, a failing exit, and
    journald-less hosts (compose/dev) all fail soft to ``501``
    (never 500). Non-int or out-of-range ``lines`` is ``422``.
    """
    del request  # Gated by the router dependency; no per-request state needed.
    argv = ["journalctl", "-u", _LOGS_UNIT, "--no-pager", "-n", str(lines)]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=_LOGS_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=501, detail="logs unavailable: journalctl timed out"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=501, detail="logs unavailable: journald is not available"
        ) from exc
    if proc.returncode != 0:
        raise HTTPException(
            status_code=501, detail="logs unavailable: journalctl failed"
        )
    return PlainTextResponse(proc.stdout.decode("utf-8", errors="replace"))
