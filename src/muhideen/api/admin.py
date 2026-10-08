"""Admin cross-cutting: token auth, audit, shared write lock, error shape.

Foundation for the token-gated admin surface (spec §1): every admin
write lives on ``admin_router`` (Bearer-gated + audited by
construction) and holds ``get_write_lock`` around read + merge +
validate + rename. Config section PATCH/validate routes (§2) live here;
public config GETs live on ``public_config_router`` (spec §1 reads stay
public, so no gate and no audit).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from muhideen.adapters.file_config import (
    FilePrayerRepo,
    _atomic_write_json,  # pyright: ignore[reportPrivateUsage]
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
)

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
