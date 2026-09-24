"""E2E SSE stream: frames, filtering, keep-alive, teardown (slice 1A-7).

Frame semantics are driven over the app's async body generator directly
(real repos over tmp SQLite + real SSEBus + real engine built exactly like
create_app) on a private event loop, because the installed
TestClient/ASGITransport buffers infinite streams, so an ASGI streaming read
never yields headers. Two live-server tests (uvicorn over real TCP) cover
what a facade cannot: a genuine client disconnect unsubscribes, and open
streams never starve sync endpoints. ASGI still covers the precondition
(503 before setup) and OpenAPI covers the text/event-stream content type.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

import muhideen.api.app as app_module
from muhideen.adapters.calc_mabims import MabimsCalcEngine
from muhideen.engine import Engine

pytestmark = pytest.mark.e2e

STREAM_COUNT = 40  # capacity of the shared anyio threadpool the sync body used to hold


def _seed_settings(surface: SimpleNamespace, **overrides: Any) -> None:
    from muhideen.core.values import Settings

    base: dict[str, Any] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
    }
    base.update(overrides)
    surface.settings_repo.save(Settings(**base))  # type: ignore[arg-type]


def _engine(surface: SimpleNamespace) -> Engine:
    tz = surface.clock.now().tzinfo
    assert tz is not None
    calc = MabimsCalcEngine(clock=surface.clock, tz=cast(ZoneInfo, tz))
    return Engine(
        settings_repo=surface.settings_repo,
        prayer_repo=surface.prayer_repo,
        clock=surface.clock,
        event_bus=surface.bus,
        calc=calc,
    )


class _Driver:
    """Step the async `_event_stream` from sync tests on one private loop.

    Each `next()` runs the generator to its next yield on the same loop, so
    tests keep their step-by-step shape (publish between reads) without an
    async pytest plugin. `close()` acloses the generator, which runs its
    `finally: unsubscribe` exactly like a server-side teardown.
    """

    def __init__(self, stream: AsyncGenerator[str]) -> None:
        self._stream = stream
        self._loop = asyncio.new_event_loop()

    def __next__(self) -> str:
        return self._loop.run_until_complete(anext(self._stream))

    def close(self) -> None:
        try:
            self._loop.run_until_complete(self._stream.aclose())
        finally:
            self._loop.close()


def _stream(surface: SimpleNamespace) -> _Driver:
    return _Driver(
        app_module._event_stream(_engine(surface), surface.clock, surface.bus)
    )


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    event = ""
    data: dict[str, object] = {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = json.loads(line.removeprefix("data:").strip())
    return event, data


def test_initial_state_frame(surface: SimpleNamespace, client: TestClient) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        frame = next(gen)
    finally:
        gen.close()
    event, data = _parse(frame)
    assert event == "state"
    assert data["time_synced"] is True
    assert data["state"] in {
        "NORMAL",
        "PRE_ADHAN",
        "ADHAN",
        "IQAMAH_COUNTDOWN",
        "SALAH_DIM",
    }


def test_sse_frames_run_next_event_off_the_event_loop(
    surface: SimpleNamespace,
) -> None:
    # next_event may probe the OS clock (timedatectl/chronyc, 10s timeouts):
    # executed on the asyncio loop inside the generator, one slow probe would
    # stall every open stream and async route at once.
    class _LoopWatchProbe:
        """TimeSyncProbe double: records which thread called it (file-local)."""

        def __init__(self) -> None:
            self.saw_running_loop = False

        def synchronized(self) -> bool:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass  # worker thread — no loop here: the safe path
            else:
                self.saw_running_loop = True
            return True

    _seed_settings(surface, lat=3.07, lon=101.69)
    probe = _LoopWatchProbe()
    tz = surface.clock.now().tzinfo
    assert tz is not None
    engine = Engine(
        settings_repo=surface.settings_repo,
        prayer_repo=surface.prayer_repo,
        clock=surface.clock,
        event_bus=surface.bus,
        calc=MabimsCalcEngine(clock=surface.clock, tz=cast(ZoneInfo, tz)),
        time_sync=probe,
    )
    gen = _Driver(app_module._event_stream(engine, surface.clock, surface.bus))
    try:
        frame = next(gen)
    finally:
        gen.close()
    event, data = _parse(frame)
    assert event == "state"
    assert data["time_synced"] is True
    assert probe.saw_running_loop is False  # must run off the loop


def test_published_state_frame(surface: SimpleNamespace, client: TestClient) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        first = next(gen)
        assert _parse(first)[0] == "state"
        surface.bus.publish("state")
        second = next(gen)
    finally:
        gen.close()
    assert _parse(second)[0] == "state"


def test_published_tick_frame(surface: SimpleNamespace, client: TestClient) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        next(gen)
        surface.bus.publish("tick")
        frame = next(gen)
    finally:
        gen.close()
    event, data = _parse(frame)
    assert event == "tick"
    assert "now" in data and "state" in data


def test_config_update_carries_changed_groups(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        next(gen)
        surface.bus.publish("config-update", ("settings",))
        frame = next(gen)
    finally:
        gen.close()
    event, data = _parse(frame)
    assert event == "config-update"
    assert data == {"changed": ["settings"]}


def test_config_update_without_groups_is_dropped(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        next(gen)
        surface.bus.publish("config-update", ())
        surface.bus.publish("tick")
        seen: list[str] = []
        for _ in range(5):
            frame = next(gen)
            seen.append(frame)
            if _parse(frame)[0] == "tick":
                break
    finally:
        gen.close()
    events = [_parse(f)[0] for f in seen]
    assert "tick" in events
    for frame in seen:
        event, data = _parse(frame)
        if event == "config-update":
            assert data.get("changed") != []


def test_unknown_event_name_is_dropped(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    try:
        next(gen)
        surface.bus.publish("bogus-event")
        surface.bus.publish("tick")
        seen: list[str] = []
        for _ in range(5):
            frame = next(gen)
            seen.append(frame)
            if _parse(frame)[0] == "tick":
                break
    finally:
        gen.close()
    events = [_parse(f)[0] for f in seen]
    assert "bogus-event" not in events
    assert "tick" in events


def test_keep_alive_comment(
    surface: SimpleNamespace, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    monkeypatch.setattr(app_module, "_KEEPALIVE_S", 0.05)
    gen = _stream(surface)
    try:
        next(gen)
        frame = next(gen)
    finally:
        gen.close()
    assert frame.startswith(": keep-alive")


def test_unsubscribe_on_disconnect(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    gen = _stream(surface)
    next(gen)
    assert surface.bus.subscriber_count >= 1
    gen.close()
    deadline = time.monotonic() + 1.0
    while surface.bus.subscriber_count != 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert surface.bus.subscriber_count == 0


def test_events_before_setup_is_503(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/api/events")
    assert response.status_code == 503


def test_real_client_disconnect_unsubscribes(
    surface: SimpleNamespace, live_server: str
) -> None:
    """A genuinely closed TCP connection must release the bus subscriber.

    Regression for the oracle-found leak: the old sync body was shielded in
    starlette's threadpool, so `finally: unsubscribe` never ran on disconnect.
    """
    _seed_settings(surface, lat=3.07, lon=101.69)
    with (
        httpx.Client(timeout=10.0) as http_client,
        http_client.stream("GET", f"{live_server}/api/events") as response,
    ):
        assert response.status_code == 200
        # Keep the iter_lines() generator suspended: finalizing it (a `break`
        # out of the for-loop) closes the httpcore connection immediately,
        # which would race the count==1 assertion below with the disconnect.
        lines = response.iter_lines()
        assert next(lines)  # first line ⇒ server produced the initial frame
        assert surface.bus.subscriber_count == 1  # stream established, still open
    # context-manager exit closes the connection (and thus the server stream)
    deadline = time.monotonic() + 2.0
    while surface.bus.subscriber_count != 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert surface.bus.subscriber_count == 0


def test_open_streams_do_not_starve_sync_endpoints(
    surface: SimpleNamespace, live_server: str
) -> None:
    """N open streams must not consume the sync handlers' threadpool.

    Regression for the oracle-found starvation: the old sync body held one
    token of anyio's shared CapacityLimiter(40) per stream while blocked in
    `queue.get`, so the 41st request (e.g. GET /api/version) timed out.
    """
    _seed_settings(surface, lat=3.07, lon=101.69)
    stop = threading.Event()
    errors: list[str] = []

    def hold(stream_id: int) -> None:
        client = httpx.Client(timeout=10.0)
        response: httpx.Response | None = None
        lines: Iterator[str] | None = None
        try:
            request = client.build_request("GET", f"{live_server}/api/events")
            response = client.send(request, stream=True)
            if response.status_code != 200:
                errors.append(f"stream {stream_id}: {response.status_code}")
                return
            lines = response.iter_lines()
            if not next(lines):
                errors.append(f"stream {stream_id}: no initial frame")
                return
            # `lines` must stay referenced while holding: finalizing the
            # iter_lines() generator closes the httpcore connection (its
            # __iter__ has a finally: close), which would drop this stream.
            stop.wait(30.0)
        except Exception as exc:  # surfaced via `errors` below
            errors.append(f"stream {stream_id}: {exc!r}")
        finally:
            del lines
            if response is not None:
                response.close()
            client.close()

    threads = [
        threading.Thread(target=hold, args=(index,), daemon=True)
        for index in range(STREAM_COUNT)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 15.0
    while surface.bus.subscriber_count < STREAM_COUNT and time.monotonic() < deadline:
        time.sleep(0.05)
    assert surface.bus.subscriber_count == STREAM_COUNT, errors

    started = time.monotonic()
    version = httpx.get(f"{live_server}/api/version", timeout=5.0)
    elapsed = time.monotonic() - started
    assert version.status_code == 200
    assert elapsed < 5.0

    stop.set()
    for thread in threads:
        thread.join(timeout=10.0)
    assert not errors, errors
    deadline = time.monotonic() + 5.0
    while surface.bus.subscriber_count != 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert surface.bus.subscriber_count == 0
