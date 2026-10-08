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

import json
import logging
import posixpath
import re
import secrets
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from muhideen.adapters.file_config import (
    FilePrayerRepo,
    _atomic_write_json,  # pyright: ignore[reportPrivateUsage]
    _playlist_from_file,  # pyright: ignore[reportPrivateUsage]
    _settings_from_config,  # pyright: ignore[reportPrivateUsage]
    load_config_file,
    load_manual_days_file,
    resolve_pins_path,
)
from muhideen.adapters.file_models import (
    REQUIRED_IQAMAH_PRAYERS,
    ConfigFile,
    IqamahRuleFile,
    ManualDay,
    PlaylistFile,
    TimeHHMM,
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
    except OSError as exc:
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
            _atomic_write_json(config_path, merged)
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
    """Strip + enforce the shared id grammar on a new value (422)."""
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
    NUL, directory-like, absolute/drive-letter, URL-structural, or
    escaping values; a legacy ``media/`` prefix is stripped.
    """
    if not value or not value.strip():
        raise ValueError("playlist item needs an image path")
    if "\x00" in value:
        raise ValueError(f"image_path must not contain NUL bytes: {value!r}")
    candidate = value.replace("\\", "/")
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
    """Auto-assign missing orders (max+1, empty → 0); within-body dupes → 422."""
    seen: set[int] = set()
    for slot in slots:
        order = slot["sort_order"]
        if order is None:
            order = (max(seen) + 1) if seen else 0
        elif order in seen:
            raise invalid("sort_order", f"duplicate sort_order in playlist: {order!r}")
        seen.add(order)
        slot["sort_order"] = order
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
        _atomic_write_json(config_path, merged)
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
