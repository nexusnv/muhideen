"""SQLite adapter: connection, single-writer Database, repos, backup.

Implements ADR-0003: stdlib ``sqlite3`` behind the core ports with WAL +
``synchronous=NORMAL`` (PRD §4.2.4). One connection serialised by one lock
is the "single writer" of PRD §7.2 — FastAPI's sync threadpool may call
from many threads at once, so every read, write, migration, and backup
goes through :class:`Database`. Transactions stay short (PRD §5.2).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the PRD §4.2.4 pragmas applied."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


class Database:
    """One connection + one lock: every access is serialised."""

    def __init__(self, path: str | Path) -> None:
        self._conn = connect(path)
        self._lock = threading.Lock()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection under the lock (no transaction needed)."""
        with self._lock:
            yield self._conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Short transaction: commit on success, roll back on any error."""
        with self._lock:
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
