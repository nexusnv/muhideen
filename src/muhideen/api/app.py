"""FastAPI surface: read-only file-config routes plus SSE (task 6).

The admin UI and database stack are gone: no sessions, no settings writes,
no playlist/display-registry/backup/logs routes. What remains is the public
display surface over the engine — prayer-day, next-event, events, version,
and the server-rendered display — all served from the JSON config file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import queue
import re
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import date, datetime, timedelta
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.blocking import BlockingScheduler
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import AfterValidator
from starlette.responses import StreamingResponse
from starlette.types import Send

from muhideen.adapters.adhan_audio import (
    ADHAN_FILENAME,
    resolve_adhan_path,
)
from muhideen.adapters.aladhan import AladhanClient
from muhideen.adapters.calc_mabims import MabimsCalcEngine
from muhideen.adapters.config_watcher import ConfigWatcher
from muhideen.adapters.file_config import (
    FilePlaylistRepo,
    FilePrayerRepo,
    FileSettingsRepo,
    load_config_file,
)
from muhideen.adapters.hijri_date import resolve_hijri
from muhideen.adapters.jakim_esolat import HttpJAKIMClient
from muhideen.adapters.scheduler import build_scheduler
from muhideen.adapters.sse_bus import SSEBus
from muhideen.adapters.system_clock import SystemClock
from muhideen.adapters.time_sync import SystemTimeSyncProbe
from muhideen.api.dto import (
    ConfigUpdateEventDTO,
    NextEventDTO,
    PrayerDayDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)
from muhideen.core.errors import ConfigError, MuhideenError, ScheduleError
from muhideen.core.ports import (
    Clock,
    PrayerRepo,
    ScheduleClient,
    SettingsRepo,
    TimeSyncProbe,
)
from muhideen.core.values import NextEvent, Playlist, PrayerDay, Settings, normalize_adhan_rel
from muhideen.domain.iqamah import card_iqamah_labels
from muhideen.domain.stage import StageOccupant, resolve_stage, stage_id
from muhideen.engine import Engine
from muhideen.views.display import build_display_context

logger = logging.getLogger(__name__)

_KEEPALIVE_S = 60.0
_POLL_S = 0.05
_ABS_PATH_RE = re.compile(r"/[^\s\"']*")
"""Absolute-path scrubber for wire details (OS errors re-embed the path)."""
_PROD_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "views" / "templates")
)


def _adhan_audio_url(media_dir: Path, rel_path: str = ADHAN_FILENAME) -> str:
    """Public URL for the configured adhan file.

    Inside the static root the file keeps its ``/static/...`` URL;
    anywhere else (both deploy targets: compose ``/media``, systemd
    ``/var/lib/muhideen/media``) it is served from the ``/media`` mount
    below — never a stale hardcoded-name fallback. A legacy
    ``media/`` prefix on the setting is stripped: it is media-relative.

    The input must already be :func:`resolve_adhan_path`-valid; empty,
    absolute, or escaping values raise :class:`ConfigError`.
    """
    try:
        rel = normalize_adhan_rel(rel_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        base = media_dir.resolve().relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return f"/media/{rel}"
    return f"/static/{(base / rel).as_posix()}"


def _media_inside_static(media_dir: Path) -> bool:
    """Whether ``media_dir`` is already covered by the ``/static`` mount."""
    try:
        media_dir.resolve().relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return False
    return True


def _public_detail(exc: ConfigError) -> str:
    """Scrub absolute filesystem paths from 503 details.

    ``load_config_file`` embeds the config path (``/etc/muhideen/...``)
    to point the SSH operator at the offending file — useful in logs,
    but filesystem-layout disclosure to every network client. Domain
    messages (no path prefix) pass through so callers keep the cause.
    """
    message = str(exc)
    path, sep, rest = message.partition(": ")
    if not sep or not Path(path).is_absolute():
        return message
    scrubbed = _ABS_PATH_RE.sub("<path>", rest)
    return f"config: {scrubbed}" if scrubbed else "config: invalid configuration"


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


@dataclass
class AppDeps:
    """Composition root dependencies for create_app (single entrypoint)."""

    settings_repo: SettingsRepo
    prayer_repo: PrayerRepo
    clock: Clock
    event_bus: SSEBus
    run_background: bool = False
    sync_client: ScheduleClient | None = None
    time_sync: TimeSyncProbe | None = None
    playlist_repo: FilePlaylistRepo | None = None
    media_dir: Path | None = None
    config_path: Path | None = None


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
    playlist_repo: FilePlaylistRepo | None = None,
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
    playlist_repo: FilePlaylistRepo | None = None,
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
    playlist_store = deps.playlist_repo
    media_dir = (
        deps.media_dir if deps.media_dir is not None else _STATIC_DIR / "uploads"
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Watch the config file, optionally run ticker+scheduler threads."""
        watcher: ConfigWatcher | None = None
        if deps.config_path is not None:
            cfg_path = Path(deps.config_path)
            buf_path: Path | None = None
            if isinstance(deps.prayer_repo, FilePrayerRepo):
                buf_path = deps.prayer_repo.buffer_path
            try:
                last_seen: str | None = hashlib.sha256(
                    cfg_path.read_bytes()
                ).hexdigest()
            except OSError:
                last_seen = None

            last_holder: list[str | None] = [last_seen]

            def _on_reload() -> None:
                """Revalidate the config; publish config-update on success.

                Read-through note: ``FileSettingsRepo``/``FilePlaylistRepo``
                re-read the file on every call, so there is nothing to swap
                — only ``FilePrayerRepo``'s in-memory ``manual_days``
                snapshot is refreshed here. Invalid edits log an error and
                keep the last-good snapshot serving with NO publish (this
                includes unordered manual-day pins, validated before the
                snapshot swap).
                Buffer-only changes are validated (log on corrupt) with NO
                publish — the next ``tick`` picks up new timetables.
                Timezone changes log a restart-required warning and keep
                the old clock (tz is fixed at boot).
                """
                try:
                    current_digest: str | None = hashlib.sha256(
                        cfg_path.read_bytes()
                    ).hexdigest()
                except OSError as exc:  # pragma: no cover - digest race guard
                    logger.error("config reload failed; keeping last-good: %s", exc)
                    return
                if current_digest == last_holder[0]:
                    if isinstance(deps.prayer_repo, FilePrayerRepo):
                        try:
                            deps.prayer_repo.validate_buffer()
                        except ConfigError as exc:
                            logger.error(
                                "prayer buffer invalid; keeping last-good: %s",
                                exc,
                            )
                    return
                try:
                    cfg = load_config_file(cfg_path)
                except ConfigError as exc:
                    logger.error("config reload failed; keeping last-good: %s", exc)
                    return
                try:
                    if isinstance(deps.settings_repo, FileSettingsRepo):
                        deps.settings_repo.load()
                    if isinstance(deps.playlist_repo, FilePlaylistRepo):
                        deps.playlist_repo.list()
                    if isinstance(deps.prayer_repo, FilePrayerRepo):
                        deps.prayer_repo.validate_pins(
                            cfg.schedule.manual_days, cfg.schedule.effective_zone
                        )
                except ConfigError as exc:
                    logger.error("config reload failed; keeping last-good: %s", exc)
                    return
                if isinstance(deps.prayer_repo, FilePrayerRepo):
                    deps.prayer_repo.set_manual_days(cfg.schedule.manual_days)
                new_tz = cfg.masjid.timezone
                current_tz = deps.clock.now().tzinfo
                current_key = getattr(current_tz, "key", str(current_tz))
                if current_key != new_tz:
                    logger.warning(
                        "timezone changed to %s; restart required to apply",
                        new_tz,
                    )
                # Record the digest only on success: a failed validation
                # (e.g. a partial pin with no provider row yet) leaves the
                # holder stale, so a later buffer arrival re-runs the full
                # path and retries the pins instead of taking the
                # digest-equal branch and leaving them unapplied.
                last_holder[0] = current_digest
                deps.event_bus.publish("config-update", ("settings",))

            watch_paths = [cfg_path, buf_path] if buf_path is not None else [cfg_path]
            watcher = ConfigWatcher(watch_paths, _on_reload)
            _app.state.config_watcher = watcher
            watcher.start()
        background: _Background | None = None
        if deps.run_background:
            if deps.sync_client is None:
                raise ValueError("sync_client is required for background wiring")
            scheduler = build_scheduler(
                client=deps.sync_client,
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
            if watcher is not None:
                watcher.stop()
            if background is not None:
                background.stop.set()
                background.ticker.join(2.0)
                background.scheduler_thread.join(0.5)
                if background.scheduler.running:
                    background.scheduler.shutdown(wait=False)
                background.scheduler_thread.join(2.0)

    app = FastAPI(
        title="muhideen",
        version=package_version("muhideen"),
        lifespan=_lifespan,
        # Docs stay public by decision: the OpenAPI document contains only
        # the read-only public schemas (prayer-day, next-event, events,
        # version, display) — no credentials, config values, or filesystem
        # material. Contract tests pin OpenAPI parity, so disabling the
        # document would trade verifiability for obscurity. Revisit only
        # if a non-public route ever returns to this surface.
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    if not _media_inside_static(media_dir):
        # Serve operator-dropped media (adhan audio, playlist images) from
        # the configured dir. Starlette answers 404 for missing files and
        # blocks traversal above the root. The mount is skipped when the
        # dir does not exist yet (fresh checkout before the first media
        # drop — the installer creates it); recreate + restart to serve.
        if media_dir.is_dir():
            app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
        else:
            logger.warning(
                "media dir %s missing: /media/* will 404 until the dir "
                "exists and the service restarts",
                media_dir,
            )

    @app.exception_handler(ConfigError)
    async def _config_error(request: Request, exc: ConfigError) -> JSONResponse:
        """Map missing/invalid configuration to HTTP 503.

        The full message (with config path) goes to the server log for
        the SSH operator; the wire detail is path-scrubbed.
        """
        logger.warning("config error serving %s: %s", request.url.path, exc)
        return JSONResponse(status_code=503, content={"detail": _public_detail(exc)})

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
        file-config ``displays.<id>`` theme overlay over the global knobs,
        then the display dim pin over the salah-dim length. Unknown ids
        render the global theme with language ``en``.
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
        language = "en"
        custom_colors: dict[str, str] | None = None
        cfg_path = deps.config_path
        if cfg_path is None and isinstance(deps.settings_repo, FileSettingsRepo):
            cfg_path = deps.settings_repo.path
        if cfg_path is not None:
            try:
                cfg = load_config_file(cfg_path)
            except ConfigError:
                return _TEMPLATES.TemplateResponse(
                    request,
                    "error.html",
                    {"code": 503, "message": "Setup required"},
                    status_code=503,
                )
            entry = cfg.displays.get(id)
            if entry is not None:
                overlay = {
                    key: value
                    for key, value in entry.theme.model_dump().items()
                    if value is not None
                }
                if overlay:
                    try:
                        effective_theme = _replace(settings.theme, **overlay)
                    except (ValueError, TypeError):
                        return _invalid_display()
                if entry.dim_minutes_override is not None:
                    dim_minutes = entry.dim_minutes_override
                    dim_source = "display"
                show_carousel = entry.carousel_enabled
                language = entry.language
                custom_colors = (
                    dict(entry.custom_colors) if entry.custom_colors else None
                )
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
        ):
            try:
                adhan_path = resolve_adhan_path(settings.adhan_audio_file, media_dir)
                if adhan_path.is_file():
                    adhan_url = _adhan_audio_url(media_dir, settings.adhan_audio_file)
            except ConfigError:
                return _TEMPLATES.TemplateResponse(
                    request,
                    "error.html",
                    {"code": 503, "message": "Setup required"},
                    status_code=503,
                )
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
                language=language,
                custom_colors=custom_colors,
            )
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
            )
        return _TEMPLATES.TemplateResponse(request, "display.html", ctx)

    return app


def create_production_app(
    config_path: str | Path,
    *,
    prayer_buffer: str | Path | None = None,
    media_dir: str | Path | None = None,
    tz: ZoneInfo = _PROD_TZ,
    run_background: bool = True,
) -> FastAPI:
    """Production composition: SystemClock + file repos + SSEBus + JAKIM client.

    The config file must exist: a missing file fails fast (the hand-edited
    file replaces the first-boot wizard, so there is nothing to seed from).
    A readable file with bad values keeps the previous contract — the clock
    falls back to ``tz`` with a logged warning and settings-dependent
    routes serve 503 until the file is fixed.
    """
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    try:
        manual_days = load_config_file(cfg_path).schedule.manual_days
    except ConfigError:
        manual_days = ()
    buffer_path = (
        Path(prayer_buffer)
        if prayer_buffer is not None
        else cfg_path.parent / "prayer_buffer.json"
    )
    # Fail loud early on the classic docker bind-mount trap: the image runs
    # as ``muhideen`` but a host-owned ``./config`` masks the image ``chown``,
    # so the sync worker's tmp+rename gets EACCES and retries forever
    # without ever caching a timetable. Warn once at boot with the fix.
    buffer_parent = buffer_path.parent
    try:
        writable = buffer_parent.exists() and os.access(buffer_parent, os.W_OK)
    except OSError:
        writable = False
    if not writable:
        logger.warning(
            "prayer buffer dir %s is not writable: the sync worker cannot "
            "cache timetables (EACCES retry loop). On compose bind mounts, "
            "run: sudo chown -R $(id -u):$(id -g) %s (or the container uid) "
            "so the service user can write prayer_buffer.json.",
            buffer_parent,
            buffer_parent,
        )
    try:
        stored_tz = FileSettingsRepo(cfg_path).load().timezone
        clock_tz = ZoneInfo(stored_tz)
    except (
        ConfigError,
        ValueError,
        ZoneInfoNotFoundError,
    ) as exc:
        logger.warning("using fallback timezone %s: %s", tz, exc)
        clock_tz = tz
    clock = SystemClock(clock_tz)
    event_bus = SSEBus()
    sync_client: ScheduleClient
    try:
        schedule = load_config_file(cfg_path).schedule
    except ConfigError:
        schedule = None
    if schedule is not None and schedule.sync_provider == "aladhan":
        # Provider switch is boot config (like the timezone): the sync
        # client is built once, so changing provider/base_url/method
        # needs a restart — hot-reload covers zone/coords/offsets only.
        sync_client = AladhanClient(
            clock=clock,
            base_url=schedule.aladhan.base_url,
            method=schedule.aladhan.method,
        )
    else:
        # JAKIM wiring, the inert fallback when the file is invalid
        # (sync skips on ConfigError until the file parses), and for
        # provider "none" (run_sync returns before touching the client).
        sync_client = HttpJAKIMClient(clock=clock)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(cfg_path),
        prayer_repo=FilePrayerRepo(buffer_path, manual_days),
        clock=clock,
        event_bus=event_bus,
        run_background=run_background,
        sync_client=sync_client,
        time_sync=SystemTimeSyncProbe(clock=clock),
        playlist_repo=FilePlaylistRepo(cfg_path),
        media_dir=Path(media_dir) if media_dir is not None else None,
        config_path=cfg_path,
    )
    return create_app(deps)
