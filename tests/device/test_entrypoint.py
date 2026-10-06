"""Device entrypoint: spawn `.venv/bin/muhideen` — ≤10s boot + render path (1A-8).

No network, no Chromium: the tmp config is patched with lat/lon settings so
`/api/next-event` resolves through the pure MABIMS calc path (PRD §5.1).
"""

from __future__ import annotations

import json
import shutil
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

from muhideen.api.dto import VersionDTO

pytestmark = pytest.mark.device

KL = timezone(timedelta(hours=8))
ZONE = "SGR01"
EXAMPLE = (
    Path(__file__).resolve().parent.parent.parent / "config" / "muhideen.example.json"
)


def _free_port() -> int:
    """Bind-then-close an ephemeral loopback port (decision 10)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_config(config_path: Path) -> None:
    """Copy the example config + persist lat/lon so calc runs without network."""
    shutil.copy(EXAMPLE, config_path)
    raw = json.loads(config_path.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    config_path.write_text(json.dumps(raw, indent=2) + "\n")


def _spawn(config_path: Path, buffer_path: Path, port: int) -> subprocess.Popen[bytes]:
    binary = Path(sys.executable).with_name("muhideen")
    assert binary.exists(), f"console script missing: {binary} (run uv sync)"
    return subprocess.Popen(
        [
            str(binary),
            "--config",
            str(config_path),
            "--prayer-buffer",
            str(buffer_path),
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Spawn the console script; ready means GET /api/version answers 200."""
    config_path = tmp_path / "muhideen.json"
    _seed_config(config_path)
    port = _free_port()
    proc = _spawn(config_path, tmp_path / "prayer_buffer.json", port)
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
    """Records `create_production_app(config, ...)` and `uvicorn.run` kwargs."""

    def __init__(self, service: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: dict[str, object] = {}
        monkeypatch.setattr(service, "create_production_app", self._app)
        monkeypatch.setattr(service, "uvicorn", SimpleNamespace(run=self._run))

    def _app(self, config: str, **kwargs: object) -> object:
        self.seen["config"] = config
        self.seen["factory_kwargs"] = kwargs
        return object()

    def _run(self, app: object, **kwargs: object) -> None:
        self.seen["run"] = kwargs


def test_main_defaults_bind_loopback_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-arg `muhideen` serves ./config/muhideen.json on 127.0.0.1:8000."""
    from muhideen import service

    spy = _ServeSpy(service, monkeypatch)
    service.main([])
    assert spy.seen["config"] == "./config/muhideen.json"
    assert spy.seen["factory_kwargs"] == {
        "prayer_buffer": "./config/prayer_buffer.json",
        "media_dir": "./media",
    }
    assert spy.seen["run"] == {"host": "127.0.0.1", "port": 8000, "workers": 1}


def test_main_argv_overrides_host_port_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemd unit's flags override every default (decision 3)."""
    from muhideen import service

    spy = _ServeSpy(service, monkeypatch)
    config = str(tmp_path / "muhideen.json")
    buffer = str(tmp_path / "prayer_buffer.json")
    media = str(tmp_path / "media")
    service.main(
        [
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--config",
            config,
            "--prayer-buffer",
            buffer,
            "--media-dir",
            media,
        ]
    )
    assert spy.seen["config"] == config
    assert spy.seen["factory_kwargs"] == {
        "prayer_buffer": buffer,
        "media_dir": media,
    }
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
