"""E2E app lifespan: migrations, flush, background wiring (slice 1A-7)."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import muhideen.api.app as app_module
from muhideen.adapters.migrate import current_version
from muhideen.api.app import AppDeps, create_app, create_production_app
from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.e2e

KL = timezone(timedelta(hours=8))
PINNED = datetime(2025, 10, 20, 12, 20, tzinfo=KL)


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

    def fetch_year(self, zone: str) -> list[PrayerDay]:
        self.calls += 1
        return []


class NaiveClock(FakeClock):
    """Clock double returning naive wall time (file-local)."""

    def now(self) -> datetime:
        return datetime(2025, 10, 20, 12, 20)


def _repo_deps(tmp_path: Path, clock: Any, **overrides: Any) -> tuple[Any, AppDeps]:
    from muhideen.adapters.sqlite_repo import (
        Database,
        SqliteDisplayRepo,
        SqlitePrayerRepo,
        SqliteSettingsRepo,
        SqliteUserRepo,
    )
    from muhideen.adapters.sse_bus import SSEBus

    db = Database(tmp_path / "muhideen.db")
    deps = AppDeps(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        display_repo=SqliteDisplayRepo(db, clock),
        user_repo=SqliteUserRepo(db),
        clock=clock,
        event_bus=SSEBus(),
        database=db,
        **overrides,
    )
    return db, deps


def _day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 20),
        zone="SGR01",
        imsak=dtime(5, 35),
        fajr=dtime(5, 45),
        syuruq=dtime(6, 55),
        dhuha=dtime(7, 25),
        dhuhr=dtime(13, 0),
        asr=dtime(15, 30),
        maghrib=dtime(18, 5),
        isha=dtime(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=PINNED,
    )


def test_lifespan_runs_migrations_at_boot(tmp_path: Path) -> None:
    from muhideen.adapters.sqlite_repo import (
        Database,
        SqliteDisplayRepo,
        SqlitePrayerRepo,
        SqliteSettingsRepo,
        SqliteUserRepo,
    )
    from muhideen.adapters.sse_bus import SSEBus

    db = Database(tmp_path / "muhideen.db")
    clock = FakeClock()
    deps = AppDeps(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        display_repo=SqliteDisplayRepo(db, clock),
        user_repo=SqliteUserRepo(db),
        clock=clock,
        event_bus=SSEBus(),
        database=db,
    )
    app = create_app(deps)
    with TestClient(app):
        pass
    assert current_version(db) == 1


def test_lifespan_flushes_heartbeats_on_shutdown(tmp_path: Path) -> None:
    from muhideen.adapters.sqlite_repo import (
        Database,
        SqliteDisplayRepo,
        SqlitePrayerRepo,
        SqliteSettingsRepo,
        SqliteUserRepo,
    )
    from muhideen.adapters.sse_bus import SSEBus

    db = Database(tmp_path / "muhideen.db")
    clock = FakeClock()
    deps = AppDeps(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        display_repo=SqliteDisplayRepo(db, clock),
        user_repo=SqliteUserRepo(db),
        clock=clock,
        event_bus=SSEBus(),
        database=db,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        with db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO displays (id, name) VALUES (?, ?)",
                ("HALL-01", "Main Hall"),
            )
        assert (
            client.post("/api/displays/heartbeat", json={"id": "HALL-01"}).status_code
            == 200
        )
    with db.read() as conn:
        row = conn.execute(
            "SELECT last_seen FROM displays WHERE id = ?", ("HALL-01",)
        ).fetchone()
    assert row is not None and row["last_seen"] is not None


def test_background_lifespan_starts_and_stops_ticker_and_scheduler(
    tmp_path: Path,
) -> None:
    from muhideen.adapters.sqlite_repo import (
        Database,
        SqliteDisplayRepo,
        SqlitePrayerRepo,
        SqliteSettingsRepo,
        SqliteUserRepo,
    )
    from muhideen.adapters.sse_bus import SSEBus

    db = Database(tmp_path / "muhideen.db")
    clock = FakeClock()
    deps = AppDeps(
        settings_repo=SqliteSettingsRepo(db),
        prayer_repo=SqlitePrayerRepo(db),
        display_repo=SqliteDisplayRepo(db, clock),
        user_repo=SqliteUserRepo(db),
        clock=clock,
        event_bus=SSEBus(),
        database=db,
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
    _, deps = _repo_deps(tmp_path, NaiveClock())
    with pytest.raises(ValueError, match="tz-aware"):
        create_app(deps)


def test_lifespan_without_database_serves_and_skips_migrate(
    tmp_path: Path,
) -> None:
    _, deps = _repo_deps(tmp_path, FakeClock())
    deps.database = None
    with TestClient(create_app(deps)) as client:
        assert client.get("/api/version").status_code == 200


def test_background_without_jakim_client_fails_fast(tmp_path: Path) -> None:
    _, deps = _repo_deps(tmp_path, FakeClock(), run_background=True)
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
    _, deps = _repo_deps(
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


def test_production_app_factory_boots_full_surface(tmp_path: Path) -> None:
    db_path = tmp_path / "prod.db"
    app = create_production_app(db_path, run_background=False)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
    from muhideen.adapters.sqlite_repo import Database

    assert current_version(Database(db_path)) == 1
