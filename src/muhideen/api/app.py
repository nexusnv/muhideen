"""FastAPI surface: real sync handlers over the engine plus SSE (slice 1A-7)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import queue
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import date, datetime, timedelta
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.blocking import BlockingScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import AfterValidator, Field
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse
from starlette.types import Send

from muhideen.adapters import backup as backup_adapter
from muhideen.adapters import logs as logs_adapter
from muhideen.adapters.adhan_audio import (
    ADHAN_FILENAME,
    MAX_ADHAN_BYTES,
    delete_adhan_audio,
    store_adhan_audio,
)
from muhideen.adapters.backup import MAX_ARCHIVE_BYTES
from muhideen.adapters.calc_mabims import MabimsCalcEngine
from muhideen.adapters.hijri_date import resolve_hijri
from muhideen.adapters.images import MAX_IMAGE_BYTES, store_image
from muhideen.adapters.jakim_esolat import HttpJAKIMClient
from muhideen.adapters.migrate import migrate
from muhideen.adapters.playlist_repo import SqlitePlaylistRepo
from muhideen.adapters.qr_code import qr_data_uri
from muhideen.adapters.scheduler import build_scheduler
from muhideen.adapters.sqlite_repo import (
    Database,
    SqliteDisplayRepo,
    SqliteDisplaySettingsRepo,
    SqlitePrayerRepo,
    SqliteSettingsRepo,
    SqliteUserRepo,
)
from muhideen.adapters.sse_bus import SSEBus
from muhideen.adapters.system_clock import SystemClock
from muhideen.adapters.time_sync import SystemTimeSyncProbe
from muhideen.api.auth import (
    ADMIN_USERNAME,
    AUTH_401_DETAIL,
    SESSION_COOKIE,
    SESSION_TTL_S,
    RateLimiter,
    SessionStore,
    is_lan,
    require_admin,
)
from muhideen.api.dto import (
    AuthRequestDTO,
    AuthResponseDTO,
    ConfigUpdateEventDTO,
    ContractDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    ManualDayDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)
from muhideen.core.errors import (
    ConfigError,
    MuhideenError,
    ScheduleError,
    SettingsNotInitializedError,
    SyncError,
)
from muhideen.core.ports import (
    Clock,
    DisplayRepo,
    JAKIMClient,
    PrayerRepo,
    SettingsRepo,
    TimeSyncProbe,
    UserRepo,
)
from muhideen.core.values import (
    CycleMode,
    MarkerName,
    NextEvent,
    Playlist,
    PlaylistItem,
    PrayerDay,
    Settings,
)
from muhideen.domain.dim import effective_dim
from muhideen.domain.fallback import is_stale
from muhideen.domain.iqamah import card_iqamah_labels
from muhideen.domain.ordering import ensure_ordered
from muhideen.domain.playlist_window import parse_window
from muhideen.domain.stage import (
    PlaylistOccupant,
    StageOccupant,
    resolve_stage,
    stage_id,
)
from muhideen.engine import Engine
from muhideen.views.admin import login_context, settings_context, setup_context
from muhideen.views.display import build_display_context

logger = logging.getLogger(__name__)

_KEEPALIVE_S = 60.0
_POLL_S = 0.05
_PROD_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "views" / "templates")
)


def _adhan_audio_url(media_dir: Path) -> str:
    """Public URL for the canonical adhan file; default when outside the static root."""
    try:
        rel = media_dir.resolve().relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return f"/static/uploads/{ADHAN_FILENAME}"
    return f"/static/{rel.as_posix()}/{ADHAN_FILENAME}"


def _require_tz_aware(value: datetime) -> datetime:
    """Reject naive datetimes; require a UTC offset on ``now``."""
    if value.tzinfo is None:
        raise ValueError("now must include a UTC offset (tz-aware ISO8601)")
    return value


def _in_quiet_hours(now: str, start: str | None, end: str | None) -> bool:
    """Report whether ``HH:MM`` ``now`` falls inside quiet ``start``–``end``.

    Overnight wraps (``22:00``–``06:00``) match ``now >= start or now < end``;
    same-day ranges match ``start <= now < end``. Unset bounds never match.
    """
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class PlaylistItemDTO(ContractDTO):
    """One playlist image slot: stored filename, on-stage seconds, order."""

    image_path: Annotated[str, Field(min_length=1, max_length=512)]
    duration_s: Annotated[int, Field(gt=0)]
    sort_order: Annotated[int, Field(ge=0)]

    @classmethod
    def from_domain(cls, item: PlaylistItem) -> PlaylistItemDTO:
        """Map a stored playlist item to its wire shape."""
        return cls(
            image_path=item.image_path,
            duration_s=item.duration_s,
            sort_order=item.sort_order,
        )

    def to_domain(self) -> PlaylistItem:
        """Map the wire item back to the validated value object."""
        return PlaylistItem(
            image_path=self.image_path,
            duration_s=self.duration_s,
            sort_order=self.sort_order,
        )


class PlaylistDTO(ContractDTO):
    """Full playlist body: schedule, cycling policy, and ordered items."""

    id: Annotated[str, Field(min_length=1, max_length=64)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    active: bool
    window_start: str | None = None
    window_end: str | None = None
    anchor_marker: str | None = None
    anchor_start_offset_min: int = 0
    anchor_stop_offset_min: int = 0
    cycle_mode: CycleMode = "indefinite"
    max_cycles: int | None = None
    items: list[PlaylistItemDTO] = Field(default_factory=list[PlaylistItemDTO])

    @classmethod
    def from_domain(cls, playlist: Playlist) -> PlaylistDTO:
        """Map a stored playlist to its wire shape."""
        return cls(
            id=playlist.id,
            title=playlist.title,
            active=playlist.active,
            window_start=playlist.window_start,
            window_end=playlist.window_end,
            anchor_marker=(
                playlist.anchor_marker.value
                if playlist.anchor_marker is not None
                else None
            ),
            anchor_start_offset_min=playlist.anchor_start_offset_min,
            anchor_stop_offset_min=playlist.anchor_stop_offset_min,
            cycle_mode=playlist.cycle_mode,
            max_cycles=playlist.max_cycles,
            items=[PlaylistItemDTO.from_domain(item) for item in playlist.items],
        )

    def to_domain(self, playlist_id: str | None = None) -> Playlist:
        """Map the wire playlist back to the validated value object.

        Raises ``ValueError`` for unknown anchors, boundary anchors, and
        malformed window bounds (mapped to 422 by the routes).
        """
        pid = playlist_id if playlist_id is not None else self.id
        anchor: MarkerName | None = None
        if self.anchor_marker is not None:
            try:
                anchor = MarkerName(self.anchor_marker.strip().lower())
            except ValueError:
                raise ValueError(
                    f"unknown playlist anchor marker: {self.anchor_marker!r}"
                ) from None
        playlist = Playlist(
            id=pid,
            title=self.title,
            active=self.active,
            window_start=self.window_start,
            window_end=self.window_end,
            anchor_marker=anchor,
            anchor_start_offset_min=self.anchor_start_offset_min,
            anchor_stop_offset_min=self.anchor_stop_offset_min,
            cycle_mode=self.cycle_mode,
            max_cycles=self.max_cycles,
            items=tuple(item.to_domain() for item in self.items),
        )
        try:
            parse_window(playlist)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return playlist


class PlaylistCreateDTO(PlaylistDTO):
    """Playlist creation body: the id is optional (server-generated)."""

    id: Annotated[str, Field(min_length=1, max_length=64)] | None = None  # type: ignore[assignment]


class ActiveToggleDTO(ContractDTO):
    """Playlist active-flag patch body."""

    active: bool


class PlaylistImageUploadDTO(ContractDTO):
    """One playlist image upload: base64 bytes plus on-stage seconds."""

    image_base64: Annotated[str, Field(min_length=1)]
    duration_s: Annotated[int, Field(gt=0)]


class AdhanAudioUploadDTO(ContractDTO):
    """Adhan MP3 upload: base64 bytes (no multipart parser on the offline footprint)."""

    audio_base64: Annotated[str, Field(min_length=1)]


class BackupRestoreDTO(ContractDTO):
    """Full-installation restore body: base64 backup zip (no multipart)."""

    archive_base64: Annotated[str, Field(min_length=1)]


class DisplayRegisterDTO(ContractDTO):
    """Register one display against an existing group."""

    id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    group_name: Annotated[str, Field(min_length=1, max_length=64)] = "Default"


class DisplayUpdateDTO(ContractDTO):
    """Per-display overrides: theme choice and group assignment."""

    current_theme: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    group_name: Annotated[str, Field(min_length=1, max_length=64)] | None = None


class DisplayGroupUpdateDTO(ContractDTO):
    """Group-level overrides: theme default and dim minutes."""

    theme: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    dim_minutes_override: Annotated[int, Field(ge=5, le=60)] | None = None
    carousel_enabled: bool | None = None


@dataclass
class AppDeps:
    """Composition root dependencies for create_app (single entrypoint)."""

    settings_repo: SettingsRepo
    prayer_repo: PrayerRepo
    display_repo: DisplayRepo
    user_repo: UserRepo
    clock: Clock
    event_bus: SSEBus
    database: Database | None = None
    run_background: bool = False
    jakim_client: JAKIMClient | None = None
    time_sync: TimeSyncProbe | None = None
    playlist_repo: SqlitePlaylistRepo | None = None
    media_dir: Path | None = None


class EventStreamResponse(StreamingResponse):
    """SSE responses: content type text/event-stream, declared in OpenAPI."""

    media_type = "text/event-stream"

    async def stream_response(self, send: Send) -> None:
        """Stream the body, then close it on every exit path.

        Starlette never closes ``body_iterator``: if a client disconnect lands
        while a frame is being sent, the generator stays suspended at its
        ``yield`` until GC and ``finally: unsubscribe`` never runs promptly.
        Closing the iterator here makes teardown deterministic (GeneratorExit
        reaches the generator's ``finally``, which is synchronous).
        """
        try:
            await super().stream_response(send)
        finally:
            iterator = self.body_iterator
            if isinstance(iterator, AsyncGenerator):
                await iterator.aclose()


def _frame(event: str, payload_json: str) -> str:
    """Render one SSE frame as ``event:`` + ``data:`` + blank line."""
    return f"event: {event}\ndata: {payload_json}\n\n"


def _preview_moment(
    engine: Engine,
    settings: Settings,
    playlists: list[Playlist],
    moment: datetime,
    days: dict[date, PrayerDay] | None = None,
) -> StageOccupant:
    """Resolve one moment's Stage occupant through the shared tick seam."""
    if days is not None and moment.date() in days:
        day = days[moment.date()]
    else:
        day = engine.resolve_day(moment.date(), settings.zone, moment).day
        if days is not None:
            days[moment.date()] = day
    event = engine.next_event(moment)
    return resolve_stage(moment, day, settings, event, playlists)


def _tick_stage(
    engine: Engine,
    settings_repo: SettingsRepo,
    event: NextEvent,
    playlist_repo: SqlitePlaylistRepo | None = None,
) -> str:
    """Stage id for one tick, resolved at the event's own pinned now."""
    settings = settings_repo.load()
    playlists = playlist_repo.list() if playlist_repo is not None else []
    return stage_id(_preview_moment(engine, settings, playlists, event.now))


@dataclass
class _Background:
    """Handles for the optional background ticker + scheduler threads."""

    stop: threading.Event
    ticker: threading.Thread
    scheduler_thread: threading.Thread
    scheduler: BlockingScheduler


class _Startable(Protocol):
    """Typed view of BlockingScheduler.start (untyped upstream)."""

    def start(self) -> None:
        """Block serving scheduled jobs until shutdown."""
        ...


def _run_ticker(engine: Engine, clock: Clock, stop: threading.Event) -> None:
    """Tick every second until ``stop``; skip pre-setup errors, log the rest."""
    while not stop.is_set():
        try:
            engine.tick()
        except MuhideenError as exc:
            logger.debug("ticker skipped (pre-setup): %s", exc)
        except Exception:
            logger.exception("ticker failed; continuing")
        if stop.wait(1.0):
            break


async def _event_stream(
    engine: Engine,
    clock: Clock,
    bus: SSEBus,
    settings_repo: SettingsRepo,
    playlist_repo: SqlitePlaylistRepo | None = None,
) -> AsyncGenerator[str]:
    """SSE body: poll the thread-safe subscriber queue without blocking a thread.

    The wait must be asynchronous: a sync generator blocked in ``queue.get``
    runs inside starlette's shared anyio threadpool, where each open stream
    holds one token of ``CapacityLimiter(40)`` — starving every sync ``def``
    endpoint at 40 concurrent streams — and the shielded block defers
    ``finally: unsubscribe`` until loop shutdown after a client disconnect.

    ``next_event`` itself runs via ``asyncio.to_thread``: it may fire the
    time-sync probe's subprocess (10s timeout each), which must never
    execute on the event loop or every stream and async route stalls with it.
    """
    subscriber = bus.subscribe()
    try:
        initial = await asyncio.to_thread(engine.next_event, clock.now())
        yield _frame(
            "state",
            StateEventDTO.from_domain(initial).model_dump_json(exclude_none=True),
        )
        loop = asyncio.get_running_loop()
        idle_since = loop.time()
        while True:
            try:
                name, changed = subscriber.get_nowait()
            except queue.Empty:
                if loop.time() - idle_since >= _KEEPALIVE_S:
                    idle_since = loop.time()
                    yield ": keep-alive\n\n"
                else:
                    await asyncio.sleep(_POLL_S)
                continue
            idle_since = loop.time()
            if name in ("state", "tick"):
                current = await asyncio.to_thread(engine.next_event, clock.now())
                if name == "state":
                    payload = StateEventDTO.from_domain(current).model_dump_json(
                        exclude_none=True
                    )
                else:
                    try:
                        stage = await asyncio.to_thread(
                            _tick_stage, engine, settings_repo, current, playlist_repo
                        )
                    except ConfigError as exc:
                        logger.error(
                            "tick stage error (config): %s — check settings;"
                            " display falls back to the route slate",
                            exc,
                        )
                        stage = "error"
                    except MuhideenError as exc:
                        logger.warning(
                            "tick stage error (schedule): %s — display falls"
                            " back to the route slate",
                            exc,
                        )
                        stage = "error"
                    payload = TickEventDTO.from_domain(
                        current,
                        stage,
                    ).model_dump_json()
                yield _frame(name, payload)
            elif name == "config-update":
                if not changed:
                    logger.debug("dropping config-update without changed groups")
                    continue
                payload = ConfigUpdateEventDTO(changed=list(changed)).model_dump_json()
                yield _frame(name, payload)
            else:
                logger.debug("dropping unknown SSE event: %s", name)
                continue
    finally:
        bus.unsubscribe(subscriber)


def create_app(deps: AppDeps) -> FastAPI:
    """Build the FastAPI app over the engine (required deps, no globals)."""
    now = deps.clock.now()
    tz = now.tzinfo
    if tz is None:
        raise ValueError("clock must be tz-aware")
    calc = MabimsCalcEngine(clock=deps.clock, tz=cast(ZoneInfo, tz))
    engine = Engine(
        settings_repo=deps.settings_repo,
        prayer_repo=deps.prayer_repo,
        clock=deps.clock,
        event_bus=deps.event_bus,
        calc=calc,
        time_sync=deps.time_sync,
    )
    sessions = SessionStore(deps.clock)
    login_limiter = RateLimiter(deps.clock)
    setup_limiter = RateLimiter(deps.clock)
    admin = require_admin(sessions)
    playlist_store = deps.playlist_repo
    if playlist_store is None and deps.database is not None:
        playlist_store = SqlitePlaylistRepo(deps.database)
    media_dir = (
        deps.media_dir if deps.media_dir is not None else _STATIC_DIR / "uploads"
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Migrate at boot, optionally run ticker+scheduler, flush on exit."""
        if deps.database is not None:
            migrate(deps.database)
        background: _Background | None = None
        if deps.run_background:
            if deps.jakim_client is None:
                raise ValueError("jakim_client is required for background wiring")
            scheduler = build_scheduler(
                client=deps.jakim_client,
                prayer_repo=deps.prayer_repo,
                settings_repo=deps.settings_repo,
                clock=deps.clock,
            )
            stop = threading.Event()
            ticker = threading.Thread(
                name="muhideen-ticker",
                daemon=True,
                target=_run_ticker,
                args=(engine, deps.clock, stop),
            )
            start = cast(_Startable, scheduler).start
            scheduler_thread = threading.Thread(
                name="muhideen-scheduler",
                daemon=True,
                target=start,
            )
            background = _Background(
                stop=stop,
                ticker=ticker,
                scheduler_thread=scheduler_thread,
                scheduler=scheduler,
            )
            _app.state.background = background
            ticker.start()
            scheduler_thread.start()
        try:
            yield
        finally:
            if background is not None:
                background.stop.set()
                background.ticker.join(2.0)
                background.scheduler_thread.join(0.5)
                if background.scheduler.running:
                    background.scheduler.shutdown(wait=False)
                background.scheduler_thread.join(2.0)
            deps.display_repo.flush()

    app = FastAPI(
        title="muhideen",
        version=package_version("muhideen"),
        lifespan=_lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.middleware("http")
    async def _docs_gate(request: Request, call_next: Any) -> Any:
        """Allow docs only from LAN hosts presenting a valid admin session."""
        if request.url.path in (
            "/docs",
            "/redoc",
            "/openapi.json",
            "/docs/oauth2-redirect",
        ):
            host = request.client.host if request.client else ""
            if not is_lan(host):
                return JSONResponse(status_code=404, content={"detail": "not found"})
            token = request.cookies.get(SESSION_COOKIE)
            if token is None or not sessions.validate(token):
                return JSONResponse(
                    status_code=401, content={"detail": AUTH_401_DETAIL}
                )
        return await call_next(request)

    @app.exception_handler(ConfigError)
    async def _config_error(request: Request, exc: ConfigError) -> JSONResponse:
        """Map missing/invalid configuration to HTTP 503."""
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ScheduleError)
    async def _schedule_error(request: Request, exc: ScheduleError) -> JSONResponse:
        """Map an unresolvable date+zone schedule to HTTP 404."""
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/api/prayer-day", response_model=PrayerDayDTO)
    def prayer_day(
        date: date,
        zone: Annotated[str, Query(min_length=1, max_length=32)],
    ) -> PrayerDayDTO:
        """Resolve one day's schedule with its staleness flag."""
        result = engine.resolve_day(date, zone, deps.clock.now())
        settings = deps.settings_repo.load()
        hijri_date = resolve_hijri(result.day.date, settings.hijri_offset)
        return PrayerDayDTO.from_domain(result.day, result.stale, hijri_date=hijri_date)

    @app.get("/api/next-event", response_model=NextEventDTO)
    def next_event(
        now: Annotated[datetime, AfterValidator(_require_tz_aware)],
    ) -> NextEventDTO:
        """Compute the display state for the pinned tz-aware ``now``."""
        return NextEventDTO.from_domain(engine.next_event(now))

    @app.get(
        "/api/events",
        response_class=EventStreamResponse,
        responses={
            200: {
                "content": {
                    "text/event-stream": {
                        "schema": {
                            # FastAPI seeds the response schema with
                            # {"type": "string"} and deep-merges `responses=`
                            # over it, so `type` must be overridden explicitly
                            # or the emitted schema is unsatisfiable. `anyOf`
                            # (not `oneOf`): a real `tick` payload also
                            # satisfies StateEventDTO's state-only shape,
                            # which would make `oneOf` reject it.
                            "type": "object",
                            "anyOf": [
                                StateEventDTO.model_json_schema(),
                                TickEventDTO.model_json_schema(),
                                ConfigUpdateEventDTO.model_json_schema(),
                            ],
                        }
                    }
                }
            }
        },
    )
    def events() -> EventStreamResponse:
        """SSE stream of state/tick/config-update events."""
        engine.next_event(deps.clock.now())
        return EventStreamResponse(
            _event_stream(
                engine, deps.clock, deps.event_bus, deps.settings_repo, playlist_store
            )
        )

    @app.post(
        "/api/displays/heartbeat",
        response_model=HeartbeatResponseDTO,
    )
    def heartbeat(
        payload: HeartbeatRequestDTO, request: Request
    ) -> HeartbeatResponseDTO:
        """Buffer one display heartbeat with its source IP."""
        ip = request.client.host if request.client else None
        registered = deps.display_repo.is_registered(payload.id)
        deps.display_repo.record_seen(payload.id, ip)
        return HeartbeatResponseDTO(ok=True, registered=registered)

    @app.get("/api/version", response_model=VersionDTO)
    def version() -> VersionDTO:
        """Report the installed package version and contract generation."""
        return VersionDTO(version=package_version("muhideen"), api="v1")

    @app.get("/display", response_class=HTMLResponse)
    def display(
        request: Request,
        id: Annotated[str, Query(min_length=1, max_length=64)],
    ) -> HTMLResponse:
        """Server-rendered public display; error slate, never blank.

        Applies per-display presentation overrides for this render only:
        ``display_settings`` theme.* rows over the global knobs, then the
        display dim pin (else the group dim pin) over the salah-dim length.
        Unknown ids render the global theme; corrupt stored rows slate 503.
        """

        def _invalid_display() -> HTMLResponse:
            """503 slate for a corrupt per-display override row."""
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Invalid display settings"},
                status_code=503,
            )

        try:
            settings = deps.settings_repo.load()
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
            )
        effective_theme = settings.theme
        dim_minutes = settings.dim_minutes_default
        dim_source = "settings"
        show_carousel = True
        if deps.database is not None:
            display_store = SqliteDisplaySettingsRepo(deps.database)
            overrides = display_store.overrides_for(id)
            theme_rows = {
                key.removeprefix("theme."): value
                for key, value in overrides.items()
                if key.startswith("theme.")
            }
            if theme_rows:
                try:
                    effective_theme = _replace(settings.theme, **theme_rows)
                except (ValueError, TypeError):
                    return _invalid_display()
            display_raw = overrides.get("dim_minutes_override")
            group_dim_raw, show_carousel = display_store.group_presentation(id)
            group_raw = group_dim_raw if display_raw is None else None
            try:
                dim_minutes, dim_source = effective_dim(
                    display_raw=display_raw,
                    group_raw=group_raw,
                    default=settings.dim_minutes_default,
                )
            except ConfigError:
                return _invalid_display()
        now = deps.clock.now()
        try:
            result = engine.resolve_day(now.date(), settings.zone, now)
            event = engine.next_event(now)
        except ScheduleError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 404, "message": "No schedule"},
                status_code=404,
            )
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
            )
        if (
            dim_source != "settings"
            and event.iqamah_at is not None
            and event.dim_until is not None
        ):
            # The pin overrides the salah-dim length for this render only;
            # global state and other displays keep the configured dim.
            event = _replace(
                event,
                dim_until=event.iqamah_at + timedelta(minutes=dim_minutes),
            )
        effective_settings = (
            settings
            if effective_theme is settings.theme
            else _replace(settings, theme=effective_theme)
        )
        event_dto = NextEventDTO.from_domain(event)
        day_dto = PrayerDayDTO.from_domain(
            result.day,
            result.stale,
            hijri_date=resolve_hijri(result.day.date, settings.hijri_offset),
        )
        try:
            labels = card_iqamah_labels(
                day_dto.date,
                {
                    "fajr": day_dto.prayers.fajr,
                    "dhuhr": day_dto.prayers.dhuhr,
                    "asr": day_dto.prayers.asr,
                    "maghrib": day_dto.prayers.maghrib,
                    "isha": day_dto.prayers.isha,
                },
                event_dto.now.tzinfo,
                {rule.prayer: rule for rule in settings.iqamah_rules},
                event_dto.next_prayer,
            )
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
            )
        adhan_url: str | None = None
        if (
            settings.adhan_audio_enabled
            and event_dto.next_prayer not in settings.adhan_muted_prayers
            and not _in_quiet_hours(
                event_dto.now.strftime("%H:%M"),
                settings.quiet_hours_start,
                settings.quiet_hours_end,
            )
            and (media_dir / ADHAN_FILENAME).exists()
        ):
            adhan_url = _adhan_audio_url(media_dir)
        try:
            ctx = build_display_context(
                day=day_dto,
                event=event_dto,
                settings=effective_settings,
                iqamah=labels,
                dim_minutes=dim_minutes,
                dim_source=dim_source,
                show_carousel=show_carousel,
                adhan_audio_url=adhan_url,
                adhan_volume=settings.adhan_volume,
            )
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
            )
        return _TEMPLATES.TemplateResponse(request, "display.html", ctx)

    @app.post("/api/auth/setup", response_model=AuthResponseDTO)
    def auth_setup(
        payload: AuthRequestDTO, request: Request, response: Response
    ) -> AuthResponseDTO:
        """Create the initial admin and issue its first session cookie."""
        ip = request.client.host if request.client else "unknown"
        if not setup_limiter.allow(ip):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if deps.user_repo.has_users():
            raise HTTPException(status_code=409, detail="already set up")
        if not deps.user_repo.create_user(ADMIN_USERNAME, payload.password):
            raise HTTPException(status_code=409, detail="already set up")
        token = sessions.issue()
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=int(SESSION_TTL_S),
        )
        return AuthResponseDTO(ok=True)

    @app.post("/api/auth/login", response_model=AuthResponseDTO)
    def auth_login(
        payload: AuthRequestDTO, request: Request, response: Response
    ) -> AuthResponseDTO:
        """Verify the admin password and issue a session cookie."""
        ip = request.client.host if request.client else "unknown"
        if not login_limiter.allow(ip):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if not deps.user_repo.verify(ADMIN_USERNAME, payload.password):
            raise HTTPException(status_code=401, detail=AUTH_401_DETAIL)
        token = sessions.issue()
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=int(SESSION_TTL_S),
        )
        return AuthResponseDTO(ok=True)

    @app.post("/api/auth/logout", response_model=AuthResponseDTO)
    def auth_logout(request: Request, response: Response) -> AuthResponseDTO:
        """Revoke the presented session and clear its cookie."""
        token = request.cookies.get(SESSION_COOKIE)
        if token is not None:
            sessions.revoke(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return AuthResponseDTO(ok=True)

    @app.get("/api/auth/session", response_model=SessionStatusDTO)
    def auth_session(request: Request) -> SessionStatusDTO:
        """Report session validity plus whether first-boot setup is pending."""
        token = request.cookies.get(SESSION_COOKIE)
        authenticated = token is not None and sessions.validate(token)
        return SessionStatusDTO(
            authenticated=authenticated,
            setup_required=not deps.user_repo.has_users(),
        )

    @app.get("/api/settings", response_model=SettingsDTO, dependencies=[Depends(admin)])
    def get_settings() -> SettingsDTO:
        """Return the full installed settings (admin session required)."""
        return SettingsDTO.from_domain(deps.settings_repo.load())

    @app.put("/api/settings", response_model=SettingsDTO, dependencies=[Depends(admin)])
    def put_settings(payload: SettingsDTO) -> SettingsDTO:
        """Replace settings atomically and fan out a config-update event."""
        try:
            settings = payload.to_domain()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        deps.settings_repo.save(settings)
        deps.event_bus.publish("config-update", ("settings",))
        return SettingsDTO.from_domain(settings)

    @app.put(
        "/api/manual-day", response_model=PrayerDayDTO, dependencies=[Depends(admin)]
    )
    def put_manual_day(payload: ManualDayDTO) -> PrayerDayDTO:
        """Pin one day's manual schedule; it outranks automatic sources."""
        settings = deps.settings_repo.load()
        now = deps.clock.now()
        try:
            day = ensure_ordered(payload.to_prayer_day(zone=settings.zone, now=now))
        except (ValueError, SyncError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        deps.prayer_repo.save_day(day)
        hijri_date = resolve_hijri(day.date, settings.hijri_offset)
        return PrayerDayDTO.from_domain(day, is_stale(day, now), hijri_date=hijri_date)

    @app.delete("/api/manual-day", dependencies=[Depends(admin)])
    def delete_manual_day(date: date) -> dict[str, Any]:
        """Release one day's manual pin; the date falls back to auto."""
        settings = deps.settings_repo.load()
        if not deps.prayer_repo.delete_day(date, settings.zone):
            raise HTTPException(status_code=404, detail="no manual pin for date")
        return {"ok": True}

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login(request: Request) -> HTMLResponse:
        """Admin login page (thin fetch client over /api/auth/login)."""
        return _TEMPLATES.TemplateResponse(request, "admin/login.html", login_context())

    @app.get("/admin", response_class=HTMLResponse)
    def admin_landing(request: Request) -> RedirectResponse:
        """Landing: setup on first boot, settings otherwise."""
        if not deps.user_repo.has_users():
            return RedirectResponse("/admin/setup")
        return RedirectResponse("/admin/settings")

    @app.get("/admin/setup", response_class=HTMLResponse, response_model=None)
    def admin_setup(request: Request) -> HTMLResponse | RedirectResponse:
        """First-boot wizard; redirects once an admin exists."""
        if deps.user_repo.has_users():
            return RedirectResponse("/admin/settings")
        return _TEMPLATES.TemplateResponse(request, "admin/setup.html", setup_context())

    @app.get("/admin/settings", response_class=HTMLResponse, response_model=None)
    def admin_settings(request: Request) -> HTMLResponse | RedirectResponse:
        """Settings page for authed admins; others go to login."""
        token = request.cookies.get(SESSION_COOKIE)
        if token is None or not sessions.validate(token):
            return RedirectResponse("/admin/login")
        try:
            dto = SettingsDTO.from_domain(deps.settings_repo.load())
        except ConfigError:
            return RedirectResponse("/admin/setup")
        rules_json = json.dumps([r.model_dump(mode="json") for r in dto.iqamah_rules])
        host = request.url.netloc or "muhideen.local:8000"
        ctx = settings_context(
            settings=dto,
            rules_json=rules_json,
            qr_data_uri=qr_data_uri(f"http://{host}/admin"),
            fallback_url=f"http://{host}/admin",
        )
        ctx["nav_base"] = ""
        ctx["version"] = package_version("muhideen")
        if deps.database is not None:
            groups = _group_list(deps.database)
            by_name = {group["name"]: group for group in groups}
            with deps.database.read() as conn:
                rows = conn.execute(
                    "SELECT id, name, ip_address, group_name, current_theme"
                    " FROM displays ORDER BY id"
                ).fetchall()
            displays: list[dict[str, Any]] = []
            for row in rows:
                group = by_name.get(str(row["group_name"]), {})
                override = group.get("dim_minutes_override")
                displays.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "ip_address": row["ip_address"],
                        "group_name": row["group_name"],
                        "current_theme": row["current_theme"],
                        "effective_dim_minutes": (
                            override
                            if override is not None
                            else dto.dim_minutes_default
                        ),
                    }
                )
            ctx["displays_json"] = json.dumps(displays)
            ctx["groups_json"] = json.dumps(groups)
        else:
            ctx["displays_json"] = "[]"
            ctx["groups_json"] = "[]"
        return _TEMPLATES.TemplateResponse(request, "admin/settings.html", ctx)

    @app.get(
        "/admin/playlists",
        response_class=HTMLResponse,
        response_model=None,
    )
    def admin_playlists(request: Request) -> HTMLResponse | RedirectResponse:
        """Playlist editor for authed admins; others go to login."""
        token = request.cookies.get(SESSION_COOKIE)
        if token is None or not sessions.validate(token):
            return RedirectResponse("/admin/login")
        stored = playlist_store
        playlists = stored.list() if stored is not None else []
        preview_error: str | None = None
        try:
            preview: dict[str, Any] | None = (
                _occupancy_preview() if stored is not None else None
            )
        except (ConfigError, ScheduleError) as exc:
            # Surface the reason instead of silent null: the editor embeds
            # the message in the preview slot (a JS-safe shape — ``stage``
            # plus an empty ``playlists`` list — since the page script only
            # null-guards before reading ``playlists``). The ``None`` slot
            # stays reserved for "no database".
            preview_error = str(exc)
            preview = {"error": preview_error, "stage": "error", "playlists": []}
        ctx: dict[str, Any] = {
            "lang": "en",
            "nav_base": "/admin/settings",
            "playlists_json": json.dumps(
                [
                    PlaylistDTO.from_domain(playlist).model_dump(mode="json")
                    for playlist in playlists
                ]
            ),
            "preview_json": json.dumps(preview) if preview is not None else "null",
            "preview_error": preview_error,
        }
        return _TEMPLATES.TemplateResponse(request, "admin/playlists.html", ctx)

    def _playlists_or_503() -> SqlitePlaylistRepo:
        """Return the playlist store; 503 when the app has no database."""
        if playlist_store is None:
            raise HTTPException(status_code=503, detail="playlist storage unavailable")
        return playlist_store

    def _registry_or_503() -> Database:
        """Return the database for display-registry reads; 503 without one."""
        if deps.database is None:
            raise HTTPException(status_code=503, detail="display registry unavailable")
        return deps.database

    def _group_list(db: Database) -> list[dict[str, Any]]:
        """List every display group with its theme and dim override."""
        with db.read() as conn:
            rows = conn.execute(
                "SELECT name, theme, carousel_enabled, dim_minutes_override"
                " FROM display_groups ORDER BY name"
            ).fetchall()
        return [
            {
                "name": row["name"],
                "theme": row["theme"],
                "carousel_enabled": bool(row["carousel_enabled"]),
                "dim_minutes_override": row["dim_minutes_override"],
            }
            for row in rows
        ]

    def _display_entry(
        db: Database, display_id: str, default_dim: int
    ) -> dict[str, Any]:
        """One display with its effective dim (group override, else default)."""
        with db.read() as conn:
            row = conn.execute(
                "SELECT d.id, d.name, d.ip_address, d.group_name,"
                " d.current_theme, g.dim_minutes_override"
                " FROM displays d LEFT JOIN display_groups g"
                " ON g.name = d.group_name WHERE d.id = ?",
                (display_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown display")
        override = row["dim_minutes_override"]
        return {
            "id": row["id"],
            "name": row["name"],
            "ip_address": row["ip_address"],
            "group_name": row["group_name"],
            "current_theme": row["current_theme"],
            "group_dim_override": override,
            "effective_dim_minutes": (
                override if override is not None else default_dim
            ),
            "dim_source": "group" if override is not None else "settings",
        }

    def _occupancy_preview() -> dict[str, Any]:
        """Server-side Stage preview: current occupant plus per-playlist next.

        ``on_stage_now`` marks the playlist holding the Stage at this
        instant; ``next_at`` is the first 5-minute sample in the coming 24h
        at which the playlist would hold it (None when never in-window).
        """
        store = _playlists_or_503()
        settings = deps.settings_repo.load()
        now = deps.clock.now()
        playlists = store.list()
        days: dict[date, PrayerDay] = {}
        current = _preview_moment(engine, settings, playlists, now, days)
        wins: dict[str, str] = {}
        for step in range(1, 289):
            moment = now + timedelta(minutes=5 * step)
            occupant = _preview_moment(engine, settings, playlists, moment, days)
            if (
                isinstance(occupant, PlaylistOccupant)
                and occupant.playlist_id not in wins
            ):
                wins[occupant.playlist_id] = moment.isoformat()
        entries = [
            {
                "id": playlist.id,
                "title": playlist.title,
                "active": playlist.active,
                "on_stage_now": (
                    isinstance(current, PlaylistOccupant)
                    and current.playlist_id == playlist.id
                ),
                "next_at": (
                    now.isoformat()
                    if isinstance(current, PlaylistOccupant)
                    and current.playlist_id == playlist.id
                    else wins.get(playlist.id)
                ),
            }
            for playlist in playlists
        ]
        return {
            "now": now.isoformat(),
            "stage": stage_id(current),
            "playlists": entries,
        }

    @app.get(
        "/api/playlists",
        dependencies=[Depends(admin)],
    )
    def list_playlists() -> dict[str, Any]:
        """List every playlist with ordered items (admin session required)."""
        store = _playlists_or_503()
        return {
            "playlists": [
                PlaylistDTO.from_domain(playlist) for playlist in store.list()
            ]
        }

    @app.post(
        "/api/playlists",
        dependencies=[Depends(admin)],
        status_code=201,
    )
    def create_playlist(payload: PlaylistCreateDTO) -> PlaylistDTO:
        """Create a playlist; the id is generated when the body omits it."""
        store = _playlists_or_503()
        pid = payload.id or uuid.uuid4().hex[:12]
        if payload.id is not None and store.get(pid) is not None:
            raise HTTPException(status_code=409, detail="playlist id already exists")
        try:
            playlist = payload.model_copy(update={"id": pid}).to_domain()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            store.save(playlist)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return PlaylistDTO.from_domain(playlist)

    @app.get(
        "/api/playlists/preview",
        dependencies=[Depends(admin)],
    )
    def preview_playlists() -> dict[str, Any]:
        """Occupancy preview computed server-side over the Stage engine."""
        return _occupancy_preview()

    @app.get(
        "/api/playlists/{playlist_id}",
        dependencies=[Depends(admin)],
    )
    def get_playlist(playlist_id: str) -> PlaylistDTO:
        """Return one playlist with ordered items."""
        playlist = _playlists_or_503().get(playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="unknown playlist")
        return PlaylistDTO.from_domain(playlist)

    @app.put(
        "/api/playlists/{playlist_id}",
        dependencies=[Depends(admin)],
    )
    def replace_playlist(playlist_id: str, payload: PlaylistDTO) -> PlaylistDTO:
        """Replace a playlist atomically (path id must match the body id)."""
        store = _playlists_or_503()
        if payload.id != playlist_id:
            raise HTTPException(status_code=422, detail="path id and body id differ")
        try:
            playlist = payload.to_domain()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if store.get(playlist_id) is None:
            raise HTTPException(status_code=404, detail="unknown playlist")
        try:
            store.save(playlist)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return PlaylistDTO.from_domain(playlist)

    @app.patch(
        "/api/playlists/{playlist_id}",
        dependencies=[Depends(admin)],
    )
    def toggle_playlist(playlist_id: str, payload: ActiveToggleDTO) -> PlaylistDTO:
        """Flip one playlist's active flag without touching its items."""
        store = _playlists_or_503()
        if not store.set_active(playlist_id, payload.active):
            raise HTTPException(status_code=404, detail="unknown playlist")
        playlist = store.get(playlist_id)
        if playlist is None:  # pragma: no cover - toggle just succeeded
            raise HTTPException(status_code=404, detail="unknown playlist")
        return PlaylistDTO.from_domain(playlist)

    @app.delete(
        "/api/playlists/{playlist_id}",
        dependencies=[Depends(admin)],
    )
    def delete_playlist(playlist_id: str) -> dict[str, Any]:
        """Delete a playlist; its items cascade."""
        if not _playlists_or_503().delete(playlist_id):
            raise HTTPException(status_code=404, detail="unknown playlist")
        return {"ok": True}

    @app.post(
        "/api/playlists/{playlist_id}/items",
        dependencies=[Depends(admin)],
        status_code=201,
    )
    def upload_playlist_item(
        playlist_id: str, payload: PlaylistImageUploadDTO
    ) -> dict[str, Any]:
        """Store one uploaded image and append it to the playlist items.

        The image travels as base64 JSON (no multipart parser on the
        offline-first footprint); bytes flow into the shared image store
        and the stored filename becomes the new playlist item. The
        playlist append is atomic, so concurrent uploads cannot lose rows.
        """
        store = _playlists_or_503()
        try:
            data = base64.b64decode(payload.image_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="invalid base64") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="image exceeds 5MB limit")
        try:
            stored = store_image(data, media_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            _, order = store.append_item(
                playlist_id,
                PlaylistItem(
                    image_path=stored.name,
                    duration_s=payload.duration_s,
                    sort_order=0,
                ),
            )
        except KeyError:
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=404, detail="unknown playlist") from None
        except ValueError as exc:
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            stored.unlink(missing_ok=True)
            raise
        # append_item assigns the order inside the transaction; re-read it
        # from the stored row via the returned playlist is unnecessary here —
        # order is the second tuple element.
        return {
            "image_path": stored.name,
            "duration_s": payload.duration_s,
            "sort_order": order,
        }

    @app.delete(
        "/api/playlists/{playlist_id}/items/{sort_order}",
        dependencies=[Depends(admin)],
    )
    def delete_playlist_item(playlist_id: str, sort_order: int) -> dict[str, Any]:
        """Remove the item at one sort position, keeping the rest in place."""
        store = _playlists_or_503()
        if store.get(playlist_id) is None:
            raise HTTPException(status_code=404, detail="unknown playlist")
        if not store.remove_item(playlist_id, sort_order):
            raise HTTPException(status_code=404, detail="unknown playlist item")
        return {"ok": True}

    @app.post(
        "/api/adhan-audio",
        dependencies=[Depends(admin)],
        status_code=201,
    )
    def upload_adhan_audio(payload: AdhanAudioUploadDTO) -> dict[str, Any]:
        """Store the adhan MP3 (base64 JSON, 10MB cap, MP3 magic only).

        Uploads replace each other under the canonical ``adhan.mp3`` name,
        so the display URL stays stable across swaps.
        """
        if len(payload.audio_base64) > (MAX_ADHAN_BYTES + 2) // 3 * 4 + 4:
            raise HTTPException(status_code=413, detail="audio exceeds 10MB limit")
        try:
            data = base64.b64decode(payload.audio_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="invalid base64") from exc
        if len(data) > MAX_ADHAN_BYTES:
            raise HTTPException(status_code=413, detail="audio exceeds 10MB limit")
        try:
            stored = store_adhan_audio(data, media_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"file": stored.name, "size": len(data)}

    @app.delete(
        "/api/adhan-audio",
        dependencies=[Depends(admin)],
    )
    def delete_adhan_audio_route() -> dict[str, Any]:
        """Remove the adhan MP3; idempotent when no file was uploaded."""
        delete_adhan_audio(media_dir)
        return {"ok": True}

    @app.get(
        "/api/displays",
        dependencies=[Depends(admin)],
    )
    def list_displays() -> dict[str, Any]:
        """List registered displays with effective theme+dim plus groups."""
        db = _registry_or_503()
        default_dim = deps.settings_repo.load().dim_minutes_default
        groups = _group_list(db)
        by_name = {group["name"]: group for group in groups}
        with db.read() as conn:
            rows = conn.execute(
                "SELECT id, name, ip_address, group_name, current_theme"
                " FROM displays ORDER BY id"
            ).fetchall()
        displays: list[dict[str, Any]] = []
        for row in rows:
            group = by_name.get(str(row["group_name"]), {})
            override = group.get("dim_minutes_override")
            displays.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "ip_address": row["ip_address"],
                    "group_name": row["group_name"],
                    "current_theme": row["current_theme"],
                    "group_dim_override": override,
                    "effective_dim_minutes": (
                        override if override is not None else default_dim
                    ),
                    "dim_source": "group" if override is not None else "settings",
                }
            )
        return {"displays": displays, "groups": groups}

    @app.post(
        "/api/displays",
        dependencies=[Depends(admin)],
        status_code=201,
    )
    def register_display(payload: DisplayRegisterDTO) -> dict[str, Any]:
        """Register one display against an existing group."""
        db = _registry_or_503()
        default_dim = deps.settings_repo.load().dim_minutes_default
        with db.read() as conn:
            group = conn.execute(
                "SELECT name FROM display_groups WHERE name = ?",
                (payload.group_name,),
            ).fetchone()
        if group is None:
            raise HTTPException(status_code=422, detail="unknown display group")
        try:
            with db.write() as conn:
                conn.execute(
                    "INSERT INTO displays (id, name, group_name) VALUES (?, ?, ?)",
                    (payload.id, payload.name, payload.group_name),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409, detail="display id already registered"
            ) from None
        entry = _display_entry(db, payload.id, default_dim)
        return entry

    @app.patch(
        "/api/displays/{display_id}",
        dependencies=[Depends(admin)],
    )
    def update_display(display_id: str, payload: DisplayUpdateDTO) -> dict[str, Any]:
        """Set per-display overrides: theme choice and group assignment.

        An explicit ``group_name`` null clears the assignment (the row
        keeps NULL, so the effective dim falls back to settings); an
        explicit ``current_theme`` null is not an update, so a body with
        nothing else to change is still 422.
        """
        db = _registry_or_503()
        default_dim = deps.settings_repo.load().dim_minutes_default
        provided = payload.model_fields_set
        has_group = "group_name" in provided
        has_theme = "current_theme" in provided and payload.current_theme is not None
        if not has_group and not has_theme:
            raise HTTPException(status_code=422, detail="nothing to update")
        with db.read() as conn:
            exists = conn.execute(
                "SELECT 1 FROM displays WHERE id = ?", (display_id,)
            ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="unknown display")
        if has_theme:
            from muhideen.core.values import THEME_CHOICES as _THEME_CHOICES

            if payload.current_theme not in _THEME_CHOICES["palette"]:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown display theme: {payload.current_theme!r}",
                )
        with db.write() as conn:
            if has_group:
                if payload.group_name is None:
                    cursor = conn.execute(
                        "UPDATE displays SET group_name = NULL WHERE id = ?",
                        (display_id,),
                    )
                    if cursor.rowcount == 0:
                        raise HTTPException(status_code=404, detail="unknown display")
                else:
                    group = conn.execute(
                        "SELECT name FROM display_groups WHERE name = ?",
                        (payload.group_name,),
                    ).fetchone()
                    if group is None:
                        raise HTTPException(
                            status_code=422, detail="unknown display group"
                        )
                    cursor = conn.execute(
                        "UPDATE displays SET group_name = ? WHERE id = ?",
                        (payload.group_name, display_id),
                    )
                    if cursor.rowcount == 0:
                        raise HTTPException(status_code=404, detail="unknown display")
            if has_theme:
                cursor = conn.execute(
                    "UPDATE displays SET current_theme = ? WHERE id = ?",
                    (payload.current_theme, display_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="unknown display")
        entry = _display_entry(db, display_id, default_dim)
        return entry

    @app.patch(
        "/api/display-groups/{name}",
        dependencies=[Depends(admin)],
    )
    def update_display_group(
        name: str, payload: DisplayGroupUpdateDTO
    ) -> dict[str, Any]:
        """Set group overrides: theme default, dim minutes, carousel flag.

        An explicit ``dim_minutes_override`` null clears the pin (SET NULL,
        falling back to settings); omission leaves it unchanged.
        """
        from muhideen.core.values import THEME_CHOICES

        db = _registry_or_503()
        provided = payload.model_fields_set
        assignments: dict[str, Any] = {}
        clear_dim = False
        if payload.theme is not None:
            if payload.theme not in THEME_CHOICES["palette"]:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown group theme: {payload.theme!r}",
                )
            assignments["theme"] = payload.theme
        if "dim_minutes_override" in provided:
            if payload.dim_minutes_override is None:
                clear_dim = True
            else:
                assignments["dim_minutes_override"] = payload.dim_minutes_override
        if payload.carousel_enabled is not None:
            assignments["carousel_enabled"] = 1 if payload.carousel_enabled else 0
        with db.write() as conn:
            if assignments or clear_dim:
                setters: list[str] = [f"{key} = ?" for key in assignments]
                values: list[Any] = list(assignments.values())
                if clear_dim:
                    setters.append("dim_minutes_override = NULL")
                cursor = conn.execute(
                    f"UPDATE display_groups SET {', '.join(setters)} WHERE name = ?",
                    (*values, name),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="unknown display group")
            else:
                exists = conn.execute(
                    "SELECT name FROM display_groups WHERE name = ?", (name,)
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="unknown display group")
        with db.read() as conn:
            row = conn.execute(
                "SELECT name, theme, carousel_enabled, dim_minutes_override"
                " FROM display_groups WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:  # pragma: no cover - update just succeeded
            raise HTTPException(status_code=404, detail="unknown display group")
        return {
            "name": row["name"],
            "theme": row["theme"],
            "carousel_enabled": bool(row["carousel_enabled"]),
            "dim_minutes_override": row["dim_minutes_override"],
        }

    @app.post(
        "/api/backup/export",
        dependencies=[Depends(admin)],
    )
    def export_backup() -> FileResponse:
        """Download the whole installation as one zip (DB snapshot + media).

        The archive holds password hashes, so treat the download as secret
        (same handling as the DB file itself). Temp-file cleanup runs as a
        background task after the download completes.
        """
        db = _registry_or_503()
        fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="muhideen-backup-")
        os.close(fd)
        dest = Path(tmp_name)
        try:
            backup_adapter.build_backup(db, media_dir, dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        stamp = deps.clock.now().strftime("%Y%m%d-%H%M%S")
        cleanup = BackgroundTask(dest.unlink, missing_ok=True)
        return FileResponse(
            path=str(dest),
            media_type="application/zip",
            filename=f"muhideen-backup-{stamp}.zip",
            background=cleanup,
        )

    @app.post(
        "/api/backup/restore",
        dependencies=[Depends(admin)],
    )
    def restore_backup(payload: BackupRestoreDTO) -> dict[str, Any]:
        """Replace the installation from a base64 backup zip (validated).

        The archive travels as base64 JSON (no multipart parser on the
        offline-first footprint); the pre-decode length bound mirrors the
        adhan upload route. ``ValueError`` from bundle validation is 400.
        """
        db = _registry_or_503()
        if len(payload.archive_base64) > (MAX_ARCHIVE_BYTES + 2) // 3 * 4 + 4:
            raise HTTPException(status_code=413, detail="backup exceeds 256MB limit")
        try:
            data = base64.b64decode(payload.archive_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="invalid base64") from exc
        fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="muhideen-restore-")
        os.close(fd)
        staged = Path(tmp_name)
        try:
            staged.write_bytes(data)
            try:
                backup_adapter.restore_backup(db, media_dir, staged, migrate_fn=migrate)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            staged.unlink(missing_ok=True)
        deps.event_bus.publish(
            "config-update", ("settings", "schedule", "media", "playlists")
        )
        return {"ok": True}

    @app.get(
        "/api/logs",
        dependencies=[Depends(admin)],
    )
    def read_service_logs(
        lines: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        """Tail the service journal; an absent journal reads available:false."""
        return logs_adapter.read_logs(lines=lines)

    return app


def create_production_app(
    db_path: str | Path,
    *,
    tz: ZoneInfo = _PROD_TZ,
    run_background: bool = True,
) -> FastAPI:
    """Production composition: SystemClock + SQLite + SSEBus + JAKIM client."""
    database = Database(db_path)
    # Migrate before reading settings: a legacy database may predate the
    # settings table entirely (lifespan re-migrates, idempotently).
    migrate(database)
    try:
        stored_tz = SqliteSettingsRepo(database).load().timezone
        clock_tz = ZoneInfo(stored_tz)
    except (
        SettingsNotInitializedError,
        ConfigError,
        ValueError,
        ZoneInfoNotFoundError,
    ) as exc:
        logger.warning("using fallback timezone %s: %s", tz, exc)
        clock_tz = tz
    clock = SystemClock(clock_tz)
    event_bus = SSEBus()
    deps = AppDeps(
        settings_repo=SqliteSettingsRepo(database),
        prayer_repo=SqlitePrayerRepo(database),
        display_repo=SqliteDisplayRepo(database, clock),
        user_repo=SqliteUserRepo(database),
        clock=clock,
        event_bus=event_bus,
        database=database,
        run_background=run_background,
        jakim_client=HttpJAKIMClient(clock=clock),
        time_sync=SystemTimeSyncProbe(clock=clock),
    )
    return create_app(deps)
