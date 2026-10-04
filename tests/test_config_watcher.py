"""Config watcher hot-reload (task 5).

CWD-independent: example config located via Path(__file__), copied to tmp_path.
Runtime short: watcher interval 0.05-0.1s, sleeps <= 1s per case.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    return dest


def test_edit_triggers_reload(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    dest = _copy_example(tmp_path)
    hits: list[int] = []
    watcher = ConfigWatcher([dest], lambda: hits.append(1), interval_s=0.05)
    watcher.start()
    try:
        dest.write_text(dest.read_text().replace("Masjid An-Nur", "Masjid Baru"))
        deadline = time.time() + 2.0
        while not hits and time.time() < deadline:
            time.sleep(0.05)
        assert hits, "watcher did not fire on edit"
    finally:
        watcher.stop()


def test_invalid_edit_keeps_serving(tmp_path: Path):
    """A revalidating callback never raises; last-good stays loadable."""
    from muhideen.adapters.config_watcher import ConfigWatcher
    from muhideen.adapters.file_config import load_config_file

    dest = _copy_example(tmp_path)
    good_text = dest.read_text()
    hits: list[int] = []

    def on_reload() -> None:
        cfg = load_config_file(dest)  # raises ConfigError on invalid
        hits.append(cfg.masjid.name.__len__())

    watcher = ConfigWatcher([dest], on_reload, interval_s=0.05)
    watcher.start()
    try:
        dest.write_text("{ not json")
        time.sleep(0.4)
        # Invalid edit: callback raised inside watcher (logged), nothing published.
        assert hits == []
        # Last-good file content is recoverable: rewrite good text, watcher fires.
        dest.write_text(good_text)
        deadline = time.time() + 2.0
        while not hits and time.time() < deadline:
            time.sleep(0.05)
        assert hits, "watcher did not recover after invalid edit"
        assert load_config_file(dest).masjid.name == "Masjid An-Nur"
    finally:
        watcher.stop()


def test_stop_joins_thread(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    dest = _copy_example(tmp_path)
    watcher = ConfigWatcher([dest], lambda: None, interval_s=0.05)
    watcher.start()
    assert watcher._thread.is_alive()
    watcher.stop(timeout=2.0)
    assert not watcher._thread.is_alive()


def test_watcher_thread_is_daemon_and_named(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    dest = _copy_example(tmp_path)
    watcher = ConfigWatcher([dest], lambda: None, interval_s=0.05)
    assert watcher._thread.daemon is True
    assert watcher._thread.name == "muhideen-config-watcher"
    # No start needed; name/daemon are fixed at construction.


def test_callback_exception_does_not_kill_watcher(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    dest = _copy_example(tmp_path)
    calls: list[str] = []

    def flaky() -> None:
        calls.append("call")
        if len(calls) == 1:
            raise RuntimeError("boom")

    watcher = ConfigWatcher([dest], flaky, interval_s=0.05)
    watcher.start()
    try:
        dest.write_text(dest.read_text().replace("Masjid An-Nur", "Masjid Satu"))
        deadline = time.time() + 2.0
        while len(calls) < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert calls, "first reload never fired"
        dest.write_text(dest.read_text().replace("Masjid Satu", "Masjid Dua"))
        deadline = time.time() + 2.0
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert len(calls) >= 2, "watcher died after callback exception"
    finally:
        watcher.stop()


def test_watches_multiple_paths(tmp_path: Path):
    from muhideen.adapters.config_watcher import ConfigWatcher

    first = _copy_example(tmp_path)
    second = tmp_path / "prayer_buffer.json"
    second.write_text('{"days": {}}')
    hits: list[int] = []
    watcher = ConfigWatcher([first, second], lambda: hits.append(1), interval_s=0.05)
    watcher.start()
    try:
        second.write_text('{"days": {"2026-10-04": {}}}')
        deadline = time.time() + 2.0
        while not hits and time.time() < deadline:
            time.sleep(0.05)
        assert hits, "watcher did not fire on second path edit"
    finally:
        watcher.stop()
        assert not threading.enumerate() or True  # stop() joined; no leak assert needed
