"""E2E app lifespan: config watcher, background wiring (task 6).

File-config only: no migrations, no database rows. The lifespan starts the
config watcher whenever a config path is wired and optionally runs the
background ticker + scheduler threads.
"""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import muhideen.api.app as app_module
from muhideen.api.app import AppDeps, create_app, create_production_app

pytestmark = pytest.mark.e2e

KL = timezone(timedelta(hours=8))
PINNED = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
EXAMPLE = (
    Path(__file__).resolve().parent.parent.parent / "config" / "muhideen.example.json"
)


class FakeClock:
    def __init__(self) -> None:
        self._now = PINNED
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono = self._mono + seconds


class FakeJAKIMClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_year(self, zone: str) -> list[Any]:
        self.calls += 1
        return []


class NaiveClock(FakeClock):
    """Clock double returning naive wall time (file-local)."""

    def now(self) -> datetime:
        return datetime(2025, 10, 20, 12, 20)


def _file_deps(tmp_path: Path, clock: Any, **overrides: Any) -> tuple[Path, AppDeps]:
    """File-backed deps over a copied example config (no database)."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus

    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    cfg = load_config_file(config_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(config_path),
        prayer_repo=FilePrayerRepo(
            tmp_path / "prayer_buffer.json", cfg.schedule.manual_days
        ),
        clock=clock,
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(config_path),
        media_dir=media_dir,
        config_path=config_path,
        **overrides,
    )
    return config_path, deps


def test_lifespan_starts_and_stops_config_watcher(tmp_path: Path) -> None:
    _, deps = _file_deps(tmp_path, FakeClock())
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
        watcher = app.state.config_watcher
        assert watcher._thread.is_alive()
    assert watcher._thread.is_alive() is False


def test_lifespan_without_config_path_skips_watcher(tmp_path: Path) -> None:
    _, deps = _file_deps(tmp_path, FakeClock())
    deps.config_path = None
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
        assert getattr(app.state, "config_watcher", None) is None


def test_background_lifespan_starts_and_stops_ticker_and_scheduler(
    tmp_path: Path,
) -> None:
    _, deps = _file_deps(
        tmp_path,
        FakeClock(),
        run_background=True,
        jakim_client=FakeJAKIMClient(),  # type: ignore[arg-type]
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
        background: SimpleNamespace = app.state.background
        background.ticker.join(0.2)
        assert background.ticker.is_alive()
        assert background.scheduler.running is True
    deadline = time.monotonic() + 2.0
    while background.ticker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert background.ticker.is_alive() is False
    assert background.scheduler.running is False


def test_create_app_rejects_naive_clock(tmp_path: Path) -> None:
    _, deps = _file_deps(tmp_path, NaiveClock())
    with pytest.raises(ValueError, match="tz-aware"):
        create_app(deps)


def test_background_without_jakim_client_fails_fast(tmp_path: Path) -> None:
    _, deps = _file_deps(tmp_path, FakeClock(), run_background=True)
    client = TestClient(create_app(deps))
    with pytest.raises(ValueError, match="jakim_client"):
        client.__enter__()


def test_background_shutdown_skips_stop_when_scheduler_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A scheduler whose thread dies before serving (broken start) must not
    # take shutdown down with it: no running scheduler, no shutdown call.
    class _DeadScheduler:
        running = False

        def start(self) -> None:
            """Never serving: shutdown must skip the stop call."""
            return

    monkeypatch.setattr(
        app_module, "build_scheduler", lambda **kwargs: _DeadScheduler()
    )
    _, deps = _file_deps(
        tmp_path, FakeClock(), run_background=True, jakim_client=FakeJAKIMClient()
    )
    with TestClient(create_app(deps)) as client:
        assert client.get("/api/version").status_code == 200


class _ScriptedEngine:
    """tick double: scripted per-call actions (file-local)."""

    def __init__(self, actions: list[str], stop: threading.Event) -> None:
        self._actions = actions
        self._stop = stop
        self.calls = 0

    def tick(self) -> None:
        self.calls += 1
        action = self._actions.pop(0) if self._actions else None
        if action == "stop":
            self._stop.set()
        elif action == "blow-up":
            self._stop.set()
            raise RuntimeError("unexpected engine failure")


def test_ticker_returns_at_once_when_already_stopped() -> None:
    stop = threading.Event()
    stop.set()
    engine = _ScriptedEngine([], stop)
    app_module._run_ticker(engine, FakeClock(), stop)  # type: ignore[arg-type]
    assert engine.calls == 0


def test_ticker_survives_unexpected_engine_error() -> None:
    stop = threading.Event()
    engine = _ScriptedEngine(["blow-up"], stop)
    app_module._run_ticker(engine, FakeClock(), stop)  # type: ignore[arg-type]
    assert engine.calls == 1


def test_ticker_loops_until_stop() -> None:
    stop = threading.Event()
    engine = _ScriptedEngine(["tick-on", "stop"], stop)
    app_module._run_ticker(engine, FakeClock(), stop)  # type: ignore[arg-type]
    assert engine.calls == 2


def test_production_clock_follows_settings_timezone(tmp_path: Path) -> None:
    import json

    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    raw = json.loads(config_path.read_text())
    raw["masjid"]["timezone"] = "Europe/London"
    raw["schedule"]["lat"] = 51.5
    raw["schedule"]["lon"] = -0.12
    config_path.write_text(json.dumps(raw, indent=2) + "\n")
    app = create_production_app(config_path, run_background=False)
    with TestClient(app) as client:
        response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 200
    assert 'data-tz="Europe/London"' in response.text


def test_production_app_factory_boots_full_surface(tmp_path: Path) -> None:
    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    app = create_production_app(config_path, run_background=False)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
    from muhideen.adapters.file_config import FileSettingsRepo

    assert FileSettingsRepo(config_path).load().zone == "SGR01"
