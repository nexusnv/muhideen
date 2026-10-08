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
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field as _field
from dataclasses import replace as _replace
from datetime import date, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Protocol, cast
from urllib.parse import quote
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
    manual_days_file_for_config,
)
from muhideen.adapters.hijri_date import resolve_hijri
from muhideen.adapters.jakim_esolat import HttpJAKIMClient
from muhideen.adapters.scheduler import build_scheduler
from muhideen.adapters.sse_bus import SSEBus
from muhideen.adapters.system_clock import SystemClock
from muhideen.adapters.time_sync import SystemTimeSyncProbe
from muhideen.api.admin import (
    admin_router,
    admin_token_file_for_config,
    public_config_router,
    read_admin_token,
)
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
from muhideen.core.values import (
    NextEvent,
    Playlist,
    PrayerDay,
    Settings,
    normalize_adhan_rel,
)
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
    whitespace-only, directory-like, absolute, URL-structural, escaping,
    or symlink-escaping values raise :class:`ConfigError`.
    """
    try:
        rel = normalize_adhan_rel(rel_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    # Symlink containment: resolve rejects media-internal symlinks pointing
    # outside the root; the URL helper must agree so a future caller using
    # only this helper cannot mint a servable /media URL for an outside file.
    # The display already calls resolve first (for is_file), so this is a
    # second cheap check on the same path.
    resolve_adhan_path(rel_path, media_dir)
    # Percent-encode the path (``/`` preserved): Uvicorn/Starlette decode
    # % escapes before StaticFiles resolves, so emitting ``%`` raw would
    # address a different file than resolve checked.
    try:
        base = media_dir.resolve().relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return f"/media/{quote(rel, safe='/')}"
    return f"/static/{quote((base / rel).as_posix(), safe='/')}"


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


def _adhan_playback_allowed(
    *,
    enabled: bool,
    next_prayer: str | None,
    muted_prayers: Sequence[str],
    now_hhmm: str,
    quiet_start: str | None,
    quiet_end: str | None,
) -> bool:
    """Pure eligibility leg of the display adhan-audio rule (#58, #60).

    ``None`` next prayer falls through as not-muted (safe default: the
    overlay only renders during ADHAN, which always has a next prayer).
    ``start == end`` quiet bounds match nothing (half-open ``[start, end)``).
    File resolution/existence stays in the route (I/O + 503 mapping).
    """
    return (
        enabled
        and next_prayer not in muted_prayers
        and not _in_quiet_hours(now_hhmm, quiet_start, quiet_end)
    )


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
    admin_token: str | None = None
    """In-memory admin Bearer token (None → gated endpoints answer 503)."""
    write_lock: threading.Lock = _field(default_factory=threading.Lock)
    """Shared process-wide config write lock for all admin writers."""


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
                    except Exception as exc:
                        logger.exception(
                            "tick stage error (unexpected): %s — display falls"
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

            def _digest(target: Path | None) -> str | None:
                """SHA256 of ``target``; ``None`` when absent/unreadable."""
                if target is None:
                    return None
                try:
                    return hashlib.sha256(target.read_bytes()).hexdigest()
                except OSError:
                    return None

            # Baseline first: these digests are the reference for the
            # explicit ``_on_reload()`` after watcher assignment, which
            # heals any edit landing between baselining and installation.
            last_cfg_holder: list[str | None] = [_digest(cfg_path)]
            initial_pins = manual_days_file_for_config(cfg_path)
            last_pins_holder: list[str | None] = [_digest(initial_pins)]
            # Every pins path ever watched (active + failed candidates).
            # Pruned to the newly active path on each successful reload so
            # abandoned candidates never accumulate wakeups.
            known_pins_holder: list[set[Path]] = [
                {initial_pins} if initial_pins is not None else set()
            ]
            watcher_holder: list[ConfigWatcher | None] = [None]

            # Reconcile boot TOCTOU: ``create_production_app`` loaded the
            # prayer-repo pins snapshot before this lifespan runs; a pins
            # edit in between would otherwise serve stale until the next
            # change. Refresh from disk here with no publish: no
            # subscribers exist yet, and the first requests read the
            # swapped snapshot directly. Any edit landing after the
            # baseline above is picked up by the explicit ``_on_reload()``
            # below.
            try:
                boot_cfg = load_config_file(cfg_path)
            except ConfigError as exc:
                logger.error(
                    "config load failed at startup; keeping boot pins: %s", exc
                )
                boot_cfg = None
            if boot_cfg is not None and isinstance(deps.prayer_repo, FilePrayerRepo):
                try:
                    deps.prayer_repo.validate_pins(
                        boot_cfg.schedule.manual_days,
                        boot_cfg.schedule.effective_zone,
                    )
                except ConfigError as exc:
                    logger.error(
                        "config pins invalid at startup; keeping boot pins: %s",
                        exc,
                    )
                else:
                    deps.prayer_repo.set_manual_days(boot_cfg.schedule.manual_days)

            def _on_reload() -> None:
                """Revalidate the config; publish config-update on success.

                Read-through note: ``FileSettingsRepo``/``FilePlaylistRepo``
                re-read the file on every call, so there is nothing to swap
                — only ``FilePrayerRepo``'s in-memory ``manual_days``
                snapshot is refreshed here (pins resolve from inline or
                from ``manual_days_file`` with identical semantics).
                Invalid edits log an error and keep the last-good snapshot
                serving with NO publish (this includes unordered manual-day
                pins and invalid pins-file edits, validated before the
                snapshot swap).
                Buffer-only changes are validated (log on corrupt) with NO
                publish — the next ``tick`` picks up new timetables.
                Timezone changes log a restart-required warning and keep
                the old clock (tz is fixed at boot).
                """
                try:
                    current_cfg: str | None = hashlib.sha256(
                        cfg_path.read_bytes()
                    ).hexdigest()
                except OSError as exc:  # pragma: no cover - digest race guard
                    logger.error("config reload failed; keeping last-good: %s", exc)
                    return
                pins_path = manual_days_file_for_config(cfg_path)
                active_watcher = watcher_holder[0]
                if pins_path is not None and active_watcher is not None:
                    active_watcher.watch(pins_path)
                    known_pins_holder[0].add(pins_path)
                current_pins = _digest(pins_path)
                if (
                    current_cfg == last_cfg_holder[0]
                    and current_pins == last_pins_holder[0]
                ):
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
                # Record digests only on success: a failed validation
                # (e.g. a partial pin with no provider row yet, or an
                # invalid pins file) leaves the holders stale, so a later
                # buffer arrival or pins fix re-runs the full path and
                # retries the pins instead of taking the digest-equal
                # branch and leaving them unapplied.
                # Prune retired pins paths only on success: the candidate
                # is watched before validation (so fixing an invalid new
                # pins file still wakes us and retries), and every
                # non-active path is unwatched once the new snapshot
                # lands — failed candidates never accumulate.
                active_set: set[Path] = {pins_path} if pins_path is not None else set()
                for stale in known_pins_holder[0] - active_set:
                    if active_watcher is not None:
                        active_watcher.unwatch(stale)
                known_pins_holder[0] = active_set
                last_cfg_holder[0] = current_cfg
                last_pins_holder[0] = current_pins
                deps.event_bus.publish("config-update", ("settings",))

            watch_paths: list[Path] = [cfg_path]
            if buf_path is not None:
                watch_paths.append(buf_path)
            if initial_pins is not None:
                watch_paths.append(initial_pins)
            watcher = ConfigWatcher(watch_paths, _on_reload)
            watcher_holder[0] = watcher
            _app.state.config_watcher = watcher
            # Heal the boot window: when clean this is a no-op (digests
            # match → buffer check only, no publish); on a real divergence
            # it converges and publishes. Runs before start() so the poll
            # thread cannot double-fire it.
            _on_reload()
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
                # Wake the scheduler before joining anything: shutdown is
                # unconditional (no `running` check) because the check races
                # a slow start winning after it — the skipped shutdown then
                # leaves the BlockingScheduler and its non-daemon executor
                # threads alive after the port closes, wedging the process
                # (#87). Shutdown from the lifespan thread never blocks
                # (wait=False wakes the scheduler loop); a never-started
                # scheduler raises and is logged, never fatal. The first
                # attempt can still land before a slow start transitions to
                # running (APScheduler raises SchedulerNotRunningError), so
                # re-attempt until the thread exits or the deadline passes —
                # a late start cannot survive teardown. Joins stay bounded
                # so teardown cannot outlive uvicorn's grace period.
                background.stop.set()
                try:
                    background.scheduler.shutdown(wait=False)
                except Exception:
                    logger.debug("scheduler shutdown failed; continuing", exc_info=True)
                background.ticker.join(2.0)
                deadline = time.monotonic() + 2.0
                while (
                    background.scheduler_thread.is_alive()
                    and time.monotonic() < deadline
                ):
                    try:
                        background.scheduler.shutdown(wait=False)
                    except Exception:
                        logger.debug(
                            "scheduler shutdown failed; continuing", exc_info=True
                        )
                    background.scheduler_thread.join(0.5)

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
        # Always mounted (mkdir -p first) so operator drops and uploads
        # serve without a restart. check_dir=False is supported by the
        # pinned Starlette; the mkdir above already guarantees the dir.
        # A path blocked by a regular file (or any other mkdir failure)
        # warns and keeps serving without /media rather than crashing.
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "media dir %s unavailable (%s): /media not mounted", media_dir, exc
            )
        else:
            app.mount(
                "/media",
                StaticFiles(directory=str(media_dir), check_dir=False),
                name="media",
            )
    app.state.admin_token = deps.admin_token
    app.state.write_lock = deps.write_lock
    app.state.config_path = deps.config_path
    app.state.prayer_repo = deps.prayer_repo
    app.state.event_bus = deps.event_bus
    # Gated admin writes + dry-run validates (audited, Bearer) alongside
    # the public config reads (spec §1 reads stay public, §2 by_alias GET).
    # Auth + mount plumbing is verified by tests/test_admin_auth.py.
    app.include_router(admin_router)
    app.include_router(public_config_router)

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
        now = deps.clock.now()
        try:
            result = engine.resolve_day(now.date(), settings.zone, now)
            # The display pin overrides the salah-dim length for this render
            # only; global state and other displays keep the configured dim.
            # Resolved through the engine seam so state selection and
            # dim_until agree (#93) — never patched post-hoc.
            event = engine.next_event(
                now,
                dim_minutes=dim_minutes if dim_source != "settings" else None,
            )
        except ScheduleError:
            if settings.lat is None and settings.lon is None:
                message = (
                    "No schedule: set coordinates in muhideen.json "
                    "or sync the zone timetable"
                )
            else:
                message = "No schedule"
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 404, "message": message},
                status_code=404,
            )
        except ConfigError:
            return _TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Setup required"},
                status_code=503,
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
        if _adhan_playback_allowed(
            enabled=settings.adhan_audio_enabled,
            next_prayer=event_dto.next_prayer,
            muted_prayers=settings.adhan_muted_prayers,
            now_hhmm=event_dto.now.strftime("%H:%M"),
            quiet_start=settings.quiet_hours_start,
            quiet_end=settings.quiet_hours_end,
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
    admin_token_file: str | Path | None = None,
) -> FastAPI:
    """Production composition: SystemClock + file repos + SSEBus + JAKIM client.

    The config file must exist: a missing file fails fast (the hand-edited
    file replaces the first-boot wizard, so there is nothing to seed from).
    A readable file with bad values keeps the previous contract — the clock
    falls back to ``tz`` with a logged warning and settings-dependent
    routes serve 503 until the file is fixed.

    The admin token is read once at startup into memory (default
    ``<config-dir>/admin_token``): missing/blank/unreadable disables the
    gated admin endpoints (503) while public reads keep serving.
    """
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    token_path = (
        Path(admin_token_file)
        if admin_token_file is not None
        else admin_token_file_for_config(cfg_path)
    )
    admin_token = read_admin_token(token_path)
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
        admin_token=admin_token,
        write_lock=threading.Lock(),
    )
    return create_app(deps)
