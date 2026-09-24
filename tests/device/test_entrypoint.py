"""Device entrypoint: spawn `.venv/bin/muhideen` — ≤10s boot + render path (1A-8).

No network, no Chromium: the tmp db is seeded with lat/lon settings so
`/api/next-event` resolves through the pure MABIMS calc path (PRD §5.1).
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database, SqliteSettingsRepo
from muhideen.api.dto import VersionDTO
from muhideen.core.values import Settings

pytestmark = pytest.mark.device

KL = timezone(timedelta(hours=8))
ZONE = "SGR01"


def _free_port() -> int:
    """Bind-then-close an ephemeral loopback port (decision 10)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_db(db_path: Path) -> None:
    """Migrate + persist lat/lon settings so calc runs without network."""
    database = Database(db_path)
    migrate(database)
    SqliteSettingsRepo(database).save(
        Settings(
            masjid_name="Masjid Test",
            zone=ZONE,
            hijri_offset=0,
            lat=3.07,
            lon=101.69,
        )
    )


def _spawn(db_path: Path, port: int) -> subprocess.Popen[bytes]:
    binary = Path(sys.executable).with_name("muhideen")
    assert binary.exists(), f"console script missing: {binary} (run uv sync)"
    return subprocess.Popen(
        [str(binary), "--db", str(db_path), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Spawn the console script; ready means GET /api/version answers 200."""
    db_path = tmp_path / "muhideen.db"
    _seed_db(db_path)
    port = _free_port()
    proc = _spawn(db_path, port)
    url = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    deadline = started + 10.0  # PRD.md:5.1 boot budget
    while True:
        if proc.poll() is not None:
            output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            pytest.fail(f"muhideen exited {proc.returncode} before ready:\n{output}")
        try:
            if httpx.get(f"{url}/api/version", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            proc.terminate()
            pytest.fail("entrypoint not ready within the 10.0s boot budget")
        time.sleep(0.1)
    ready_s = time.monotonic() - started
    yield SimpleNamespace(url=url, ready_s=ready_s)
    if proc.poll() is None:
        proc.terminate()
    rc = proc.wait(timeout=5.0)  # TimeoutExpired here would be a hung shutdown
    output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
    assert "Finished server process" in output, f"ungraceful shutdown:\n{output}"
    # uvicorn re-raises the captured SIGTERM after its graceful shutdown, so a
    # clean stop reports -SIGTERM; 0 covers a shutdown that beat the handler.
    assert rc in (0, -signal.SIGTERM), f"unexpected exit {rc}:\n{output}"


class _ServeSpy:
    """Records `create_production_app(db)` and `uvicorn.run` kwargs (decision 3)."""

    def __init__(self, service: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: dict[str, object] = {}
        monkeypatch.setattr(service, "create_production_app", self._app)
        monkeypatch.setattr(service, "uvicorn", SimpleNamespace(run=self._run))

    def _app(self, db: str) -> object:
        self.seen["db"] = db
        return object()

    def _run(self, app: object, **kwargs: object) -> None:
        self.seen["run"] = kwargs


def test_main_defaults_bind_loopback_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-arg `muhideen` serves ./muhideen.db on 127.0.0.1:8000 (decision 3)."""
    from muhideen import service

    spy = _ServeSpy(service, monkeypatch)
    service.main([])
    assert spy.seen["db"] == "./muhideen.db"
    assert spy.seen["run"] == {"host": "127.0.0.1", "port": 8000, "workers": 1}


def test_main_argv_overrides_host_port_and_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemd unit's flags override every default (decision 3)."""
    from muhideen import service

    spy = _ServeSpy(service, monkeypatch)
    db = str(tmp_path / "state.db")
    service.main(["--host", "0.0.0.0", "--port", "9000", "--db", db])
    assert spy.seen["db"] == db
    assert spy.seen["run"] == {"host": "0.0.0.0", "port": 9000, "workers": 1}


def test_entrypoint_ready_within_10s_with_version_payload(
    server: SimpleNamespace,
) -> None:
    assert server.ready_s <= 10.0
    response = httpx.get(f"{server.url}/api/version", timeout=2.0)
    assert response.status_code == 200
    payload = VersionDTO.model_validate(response.json())
    assert payload.api == "v1"
    assert payload.version


def test_entrypoint_serves_render_payload_without_browser(
    server: SimpleNamespace,
) -> None:
    """The render payload over real TCP — no Chromium in the path (exit gate)."""
    now = datetime.now(tz=KL).isoformat()
    response = httpx.get(
        f"{server.url}/api/next-event", params={"now": now}, timeout=5.0
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["state"], str)
    assert "next_prayer" in data
    # The value is environment-dependent (timedatectl may report unsynced) —
    # assert presence + type only; the deterministic false path is e2e (decision 10).
    assert isinstance(data["time_synced"], bool)
