"""Poll-based config file watcher (no watchdog dependency).

``ConfigWatcher`` polls the watched JSON files and calls ``on_reload``
once per detected change. Identity is ``(st_mtime_ns, st_size, sha256)``:
``mtime_ns`` alone can miss a rewrite that lands inside one filesystem
timestamp tick (copy + edit microseconds apart), so size and a content
hash back it up. The callback runs inside ``try/except`` (exceptions are
logged, the thread keeps serving), so an invalid hand-edit never kills
the server — the lifespan ``on_reload`` additionally revalidates via
:func:`load_config_file` and keeps the last-good snapshot without
publishing on failure.

``FileSettingsRepo``/``FilePlaylistRepo`` are read-through per call, so
there is nothing to swap on reload; ``FilePrayerRepo`` caches only the
``manual_days`` snapshot (buffer rows are read per call), refreshed via
:meth:`FilePrayerRepo.set_manual_days` by the lifespan callback. When
``schedule.manual_days_file`` is set, the lifespan also watches the
resolved pins file so pins edits hot-reload like main-config edits.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

_Snapshot = tuple[int, int, str] | None


def _snapshot(path: Path) -> _Snapshot:
    """Identity of ``path``: ``(mtime_ns, size, sha256)``; ``None`` if unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, digest)


class ConfigWatcher:
    """Poll watched files; call ``on_reload`` on change.

    Stdlib ``threading`` only. Missing/unreadable files are skipped (a
    missing buffer file at boot is an empty cache, not an error); a file
    that appears later fires once.
    """

    def __init__(
        self,
        paths: Sequence[str | Path],
        on_reload: Callable[[], None],
        interval_s: float = 1.0,
    ) -> None:
        """Hold paths, callback, and poll interval (thread not started)."""
        self._paths = [Path(p) for p in paths]
        self._on_reload = on_reload
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(
            name="muhideen-config-watcher",
            daemon=True,
            target=self._run,
        )
        self._snaps: dict[Path, _Snapshot] = {
            path: _snapshot(path) for path in self._paths
        }

    def start(self) -> None:
        """Start the daemon polling thread."""
        self._thread.start()

    def watch(self, path: str | Path) -> None:
        """Start watching one more file (pins-file reference changes).

        Idempotent: re-adding a watched path is a no-op. Called from the
        lifespan reload when the main config starts pointing at a new
        pins file so edits to it hot-reload without a restart.
        """
        candidate = Path(path)
        if candidate not in self._paths:
            self._paths.append(candidate)
            self._snaps[candidate] = _snapshot(candidate)

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the thread and join it (bounded by ``timeout``)."""
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        """Poll until stopped; log callback errors, keep serving."""
        while not self._stop.wait(self._interval):
            for path in list(self._paths):
                snap = _snapshot(path)
                if snap is None:
                    continue
                if self._snaps.get(path) != snap:
                    self._snaps[path] = snap
                    try:
                        self._on_reload()
                    except Exception:
                        logger.exception("config reload failed; keeping last-good")
