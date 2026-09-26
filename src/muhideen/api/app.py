"""FastAPI surface: real sync handlers over the engine plus SSE (slice 1A-7)."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any, Protocol, cast
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator
from starlette.responses import StreamingResponse
from starlette.types import Send

from muhideen.adapters.calc_mabims import MabimsCalcEngine
from muhideen.adapters.hijri_date import resolve_hijri
from muhideen.adapters.jakim_esolat import HttpJAKIMClient
from muhideen.adapters.migrate import migrate
from muhideen.adapters.scheduler import build_scheduler
from muhideen.adapters.sqlite_repo import (
    Database,
    SqliteDisplayRepo,
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
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)
from muhideen.core.errors import ConfigError, MuhideenError, ScheduleError
from muhideen.core.ports import (
    Clock,
    DisplayRepo,
    JAKIMClient,
    PrayerRepo,
    SettingsRepo,
    TimeSyncProbe,
    UserRepo,
)
from muhideen.engine import Engine

logger = logging.getLogger(__name__)

_KEEPALIVE_S = 60.0
_POLL_S = 0.05
_PROD_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _require_tz_aware(value: datetime) -> datetime:
    """Reject naive datetimes; require a UTC offset on ``now``."""
    if value.tzinfo is None:
        raise ValueError("now must include a UTC offset (tz-aware ISO8601)")
    return value


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
    engine: Engine, clock: Clock, bus: SSEBus
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
                    payload = TickEventDTO.from_domain(current).model_dump_json()
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
        return PrayerDayDTO.from_domain(
            result.day, result.stale, hijri_date=hijri_date
        )

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
        return EventStreamResponse(_event_stream(engine, deps.clock, deps.event_bus))

    @app.post(
        "/api/displays/heartbeat",
        response_model=HeartbeatResponseDTO,
    )
    def heartbeat(
        payload: HeartbeatRequestDTO, request: Request
    ) -> HeartbeatResponseDTO:
        """Buffer one display heartbeat with its source IP."""
        ip = request.client.host if request.client else None
        deps.display_repo.record_seen(payload.id, ip)
        return HeartbeatResponseDTO(ok=True)

    @app.get("/api/version", response_model=VersionDTO)
    def version() -> VersionDTO:
        """Report the installed package version and contract generation."""
        return VersionDTO(version=package_version("muhideen"), api="v1")

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

    return app


def create_production_app(
    db_path: str | Path,
    *,
    tz: ZoneInfo = _PROD_TZ,
    run_background: bool = True,
) -> FastAPI:
    """Production composition: SystemClock + SQLite + SSEBus + JAKIM client."""
    clock = SystemClock(tz)
    database = Database(db_path)
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
