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
from muhideen.core.values import Settings

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


class FakeScheduleClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_year(self, settings: Settings) -> list[Any]:
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
        sync_client=FakeScheduleClient(),  # type: ignore[arg-type]
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


def test_background_without_sync_client_fails_fast(tmp_path: Path) -> None:
    _, deps = _file_deps(tmp_path, FakeClock(), run_background=True)
    client = TestClient(create_app(deps))
    with pytest.raises(ValueError, match="sync_client"):
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
        tmp_path, FakeClock(), run_background=True, sync_client=FakeScheduleClient()
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


def test_partial_pin_applies_when_its_row_arrives(tmp_path: Path) -> None:
    """A partial pin added before its row syncs applies once the row lands.

    Regression: the reload digest is recorded only after pin validation
    succeeds — otherwise the later buffer arrival takes the digest-equal
    branch and never retries the pins.
    """
    import json as json_mod
    import queue
    from datetime import date as day_date
    from datetime import time as day_time

    from muhideen.adapters.file_config import FilePrayerRepo, load_config_file
    from muhideen.core.values import PrayerDay, ScheduleSource

    config_path, deps = _file_deps(tmp_path, FakeClock())
    assert isinstance(deps.prayer_repo, FilePrayerRepo)
    app = create_app(deps)
    with TestClient(app) as client:
        subscriber = deps.event_bus.subscribe()
        try:
            # Phase 1: add a partial pin with no provider row for its date.
            raw = json_mod.loads(config_path.read_text())
            pin = raw["schedule"]["manual_days"][0]
            raw["schedule"]["manual_days"] = [
                {"date": pin["date"], "maghrib": pin["maghrib"]}
            ]
            config_path.write_text(json_mod.dumps(raw))
            # The reload attempt fails loudly with no publish...
            with pytest.raises(queue.Empty):
                subscriber.get(timeout=3.0)
            zone = load_config_file(config_path).schedule.effective_zone
            pin_day = day_date.fromisoformat(pin["date"])
            # ...and the live snapshot still holds the last-good pins.
            assert deps.prayer_repo._manual_days[0].fajr is not None

            # Phase 2: the provider row arrives — pins retry and publish.
            deps.prayer_repo.save_day(
                PrayerDay(
                    date=pin_day,
                    zone=zone,
                    imsak=day_time(5, 48),
                    fajr=day_time(5, 58),
                    syuruq=day_time(7, 5),
                    dhuha=day_time(7, 33),
                    dhuhr=day_time(13, 15),
                    asr=day_time(16, 30),
                    maghrib=day_time(19, 15),
                    isha=day_time(20, 30),
                    source=ScheduleSource.JAKIM,
                    fetched_at=PINNED,
                )
            )
            name, changed = subscriber.get(timeout=8.0)
            assert (name, changed) == ("config-update", ("settings",))
            completed = deps.prayer_repo.get_pin(pin_day, zone)
            assert completed is not None
            assert completed.source is ScheduleSource.MANUAL
            assert completed.maghrib == day_time(19, 15)
            assert completed.fajr == day_time(5, 58)

            # Phase 3: a corrupt buffer is logged with no publish, no outage.
            (tmp_path / "prayer_buffer.json").write_text("{bogus")
            with pytest.raises(queue.Empty):
                subscriber.get(timeout=2.5)
            assert client.get("/api/version").status_code == 200
        finally:
            deps.event_bus.unsubscribe(subscriber)


def test_boot_reconciles_pins_edited_before_lifespan(tmp_path: Path) -> None:
    """Pins edited between repo construction and lifespan start apply at boot.

    ``create_production_app`` loads the repo snapshot before the lifespan
    records its watcher baseline; without a startup reconciliation the
    baseline would match the edited file while the repo still holds the
    older pins, serving stale until the next change.
    """
    import json as json_mod
    from datetime import date as day_date
    from datetime import time as day_time

    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus

    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    raw = json_mod.loads(config_path.read_text())
    pin = dict(raw["schedule"]["manual_days"][0])
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json_mod.dumps([pin]))
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = pins_path.name
    config_path.write_text(json_mod.dumps(raw))
    cfg = load_config_file(config_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(config_path),
        prayer_repo=FilePrayerRepo(
            tmp_path / "prayer_buffer.json", cfg.schedule.manual_days
        ),
        clock=FakeClock(),
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(config_path),
        media_dir=media_dir,
        config_path=config_path,
    )
    # The pins file changes after the repo snapshot is built but before
    # the lifespan runs (the production boot window).
    updated = dict(pin, maghrib="19:09")
    pins_path.write_text(json_mod.dumps([updated]))
    app = create_app(deps)
    with TestClient(app):
        assert isinstance(deps.prayer_repo, FilePrayerRepo)
        zone = load_config_file(config_path).schedule.effective_zone
        served = deps.prayer_repo.get_pin(day_date.fromisoformat(pin["date"]), zone)
        assert served is not None
        assert served.maghrib == day_time(19, 9)


def test_pins_file_hot_reload_and_invalid_keeps_last_good(tmp_path: Path) -> None:
    """Pins-file edits hot-reload; invalid pins keep last-good with no publish."""
    import json as json_mod
    import queue
    from datetime import date as day_date
    from datetime import time as day_time

    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus

    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    raw = json_mod.loads(config_path.read_text())
    pin = dict(raw["schedule"]["manual_days"][0])
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json_mod.dumps([pin]))
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = pins_path.name
    config_path.write_text(json_mod.dumps(raw))
    cfg = load_config_file(config_path)
    assert len(cfg.schedule.manual_days) == 1
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(config_path),
        prayer_repo=FilePrayerRepo(
            tmp_path / "prayer_buffer.json", cfg.schedule.manual_days
        ),
        clock=FakeClock(),
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(config_path),
        media_dir=media_dir,
        config_path=config_path,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
        subscriber = deps.event_bus.subscribe()
        try:
            zone = load_config_file(config_path).schedule.effective_zone
            pin_day = day_date.fromisoformat(pin["date"])
            assert isinstance(deps.prayer_repo, FilePrayerRepo)
            assert deps.prayer_repo.get_pin(pin_day, zone) is not None

            updated = dict(pin, maghrib="19:09")
            pins_path.write_text(json_mod.dumps([updated]))
            name, changed = subscriber.get(timeout=8.0)
            assert (name, changed) == ("config-update", ("settings",))
            completed = deps.prayer_repo.get_pin(pin_day, zone)
            assert completed is not None
            assert completed.maghrib == day_time(19, 9)

            pins_path.write_text("{ not json")
            with pytest.raises(queue.Empty):
                subscriber.get(timeout=2.5)
            assert deps.prayer_repo.get_pin(pin_day, zone) is not None
            assert deps.prayer_repo.get_pin(pin_day, zone).maghrib == day_time(19, 9)

            pins_path.write_text(json_mod.dumps([pin]))
            name, changed = subscriber.get(timeout=8.0)
            assert (name, changed) == ("config-update", ("settings",))
            assert deps.prayer_repo.get_pin(pin_day, zone).maghrib == day_time(19, 15)
        finally:
            deps.event_bus.unsubscribe(subscriber)


def test_failed_pins_candidate_is_unwatched_after_recovery(
    tmp_path: Path,
) -> None:
    """A pins path left behind by a failed reload is pruned on recovery.

    The failed candidate stays watched while it fails (so fixing it still
    retries), but once a later valid pins file lands, edits to the
    abandoned path must no longer wake the reload path.
    """
    import json as json_mod
    import queue
    from datetime import date as day_date
    from datetime import time as day_time

    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus

    config_path = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, config_path)
    raw = json_mod.loads(config_path.read_text())
    pin = dict(raw["schedule"]["manual_days"][0])
    pins_good = tmp_path / "pins-good.json"
    pins_good.write_text(json_mod.dumps([pin]))
    raw["schedule"]["manual_days"] = []
    raw["schedule"]["manual_days_file"] = pins_good.name
    config_path.write_text(json_mod.dumps(raw))
    cfg = load_config_file(config_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(config_path),
        prayer_repo=FilePrayerRepo(
            tmp_path / "prayer_buffer.json", cfg.schedule.manual_days
        ),
        clock=FakeClock(),
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(config_path),
        media_dir=media_dir,
        config_path=config_path,
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200
        watcher = app.state.config_watcher
        subscriber = deps.event_bus.subscribe()
        try:
            # Point at an invalid pins file: reload fails, no publish,
            # but the candidate stays watched so fixing it would retry.
            pins_bad = tmp_path / "pins-bad.json"
            pins_bad.write_text("{ not json")
            raw = json_mod.loads(config_path.read_text())
            raw["schedule"]["manual_days_file"] = pins_bad.name
            config_path.write_text(json_mod.dumps(raw))
            with pytest.raises(queue.Empty):
                subscriber.get(timeout=3.0)
            assert Path(pins_bad) in watcher._paths

            # Recover onto a second valid pins file with a distinct pin.
            pins_next = tmp_path / "pins-next.json"
            pins_next.write_text(json_mod.dumps([dict(pin, maghrib="19:09")]))
            raw = json_mod.loads(config_path.read_text())
            raw["schedule"]["manual_days_file"] = pins_next.name
            config_path.write_text(json_mod.dumps(raw))
            name, changed = subscriber.get(timeout=8.0)
            assert (name, changed) == ("config-update", ("settings",))
            zone = load_config_file(config_path).schedule.effective_zone
            pin_day = day_date.fromisoformat(pin["date"])
            assert isinstance(deps.prayer_repo, FilePrayerRepo)
            served = deps.prayer_repo.get_pin(pin_day, zone)
            assert served is not None
            assert served.maghrib == day_time(19, 9)
            # The abandoned candidate is pruned; the active path stays.
            assert Path(pins_bad) not in watcher._paths
            assert Path(pins_good) not in watcher._paths
            assert Path(pins_next) in watcher._paths
        finally:
            deps.event_bus.unsubscribe(subscriber)
