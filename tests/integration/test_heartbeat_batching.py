"""60s heartbeat batching over SQLite (slice 1A-5, Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database, SqliteDisplayRepo
from muhideen.core.ports import DisplayRepo

pytestmark = pytest.mark.integration

_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_T0 = datetime(2025, 10, 20, 12, 0, tzinfo=_TZ)


class FakeClock:
    """File-local pinned clock (TESTING_STRATEGY.md:17-18)."""

    def __init__(self) -> None:
        self._now = _T0
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    with database.write() as conn:
        conn.executemany(
            "INSERT INTO displays (id, name) VALUES (?, ?)",
            [("HALL-01", "Hall 1"), ("HALL-02", "Lobby")],
        )
    return database


def _last_seen(db: Database, display_id: str) -> str | None:
    with db.read() as conn:
        row = conn.execute(
            "SELECT last_seen FROM displays WHERE id = ?", (display_id,)
        ).fetchone()
    return row[0] if row is not None else None


def test_record_seen_buffers_without_writing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.1")
    repo.record_seen("HALL-02", None)
    assert _last_seen(db, "HALL-01") is None
    assert _last_seen(db, "HALL-02") is None


def test_window_open_at_30s(tmp_path: Path) -> None:
    # 30s heartbeats must never write through (PRD.md:157 batching).
    db = _db(tmp_path)
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.1")
    clock.advance(30)
    repo.record_seen("HALL-01", "10.0.0.1")
    assert _last_seen(db, "HALL-01") is None


def test_next_record_after_60s_flushes_every_buffered_row(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.1")  # buffered at t0
    clock.advance(30)
    repo.record_seen("HALL-02", None)  # buffered at t0+30
    clock.advance(31)  # 61s after construction — window closed
    repo.record_seen("HALL-01", "10.0.0.1")  # triggers the batch flush
    assert _last_seen(db, "HALL-01") == _T0.replace(minute=1, second=1).isoformat()
    assert _last_seen(db, "HALL-02") == _T0.replace(minute=0, second=30).isoformat()


def test_explicit_flush_returns_rows_and_clears_buffer(tmp_path: Path) -> None:
    db = _db(tmp_path)
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.1")
    repo.record_seen("HALL-02", None)
    assert repo.flush() == 2
    assert repo.flush() == 0
    assert _last_seen(db, "HALL-01") is not None
    assert _last_seen(db, "HALL-02") is not None


def test_unknown_display_id_is_dropped_not_inserted(tmp_path: Path) -> None:
    # Pre-registered IDs only (docs/api-contract.md:60).
    db = _db(tmp_path)
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.1")
    repo.record_seen("GHOST-99", "10.0.0.9")
    assert repo.flush() == 1
    with db.read() as conn:
        ids = {row[0] for row in conn.execute("SELECT id FROM displays")}
    assert ids == {"HALL-01", "HALL-02"}


def test_flush_updates_ip_and_last_seen_for_known_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.write() as conn:
        conn.execute(
            "UPDATE displays SET ip_address = '10.0.0.1',"
            " last_seen = '2025-01-01T00:00:00' WHERE id = 'HALL-01'"
        )
    clock = FakeClock()
    repo = SqliteDisplayRepo(db, clock)
    repo.record_seen("HALL-01", "10.0.0.77")
    assert repo.flush() == 1
    with db.read() as conn:
        row = conn.execute(
            "SELECT ip_address, last_seen FROM displays WHERE id = 'HALL-01'"
        ).fetchone()
    assert row is not None
    assert row[0] == "10.0.0.77"
    assert row[1] == clock.now().isoformat()  # stamped by the injected Clock


def test_sqlite_display_repo_satisfies_port(tmp_path: Path) -> None:
    db = _db(tmp_path)
    clock = FakeClock()
    assert isinstance(SqliteDisplayRepo(db, clock), DisplayRepo)
