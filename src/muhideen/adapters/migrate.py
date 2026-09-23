"""Hand-rolled migrations: numbered ``*.sql`` applied in order (ADR-0003).

``NNNN_slug.sql`` is the up-migration for version ``NNNN``;
``NNNN_slug.down.sql`` rolls it back. Version state lives in
``PRAGMA user_version`` (an integer that cannot be parameter-bound, so the
runner sets it from the integer filename segment). Migration files must be
idempotent: a crash between the script and the version bump re-runs them.
"""

from __future__ import annotations

from pathlib import Path

from muhideen.adapters.sqlite_repo import Database
from muhideen.core.errors import MuhideenError

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _up_files() -> list[Path]:
    return sorted(
        path for path in _MIGRATIONS_DIR.glob("*.sql") if ".down." not in path.name
    )


def _version_of(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def _user_version_stmt(version: int) -> str:
    """Build the ``PRAGMA user_version`` setter.

    SQLite pragmas reject bound parameters (``PRAGMA user_version = ?``
    is a syntax error), so the value must be part of the statement text.
    Only an exact built-in ``int`` is admitted — an ``int`` subclass (or
    any other object) could override ``__format__`` and emit arbitrary
    text — and the ``d`` spec then renders it as decimal digits, so no
    untrusted text can ever reach the SQL.
    """
    if type(version) is not int:
        raise TypeError("version must be a built-in int")
    return f"PRAGMA user_version = {version:d}"


def current_version(db: Database) -> int:
    """Read ``PRAGMA user_version`` (0 on a never-migrated database)."""
    with db.read() as conn:
        rows = conn.execute("PRAGMA user_version").fetchall()
    return int(rows[0][0])


def migrate(db: Database) -> int:
    """Apply every pending up-migration; return the resulting version."""
    version = current_version(db)
    for path in _up_files():
        file_version = _version_of(path)
        if file_version <= version:
            continue
        with db.write() as conn:
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(_user_version_stmt(file_version))
        version = file_version
    return version


def migrate_down(db: Database, target: int = 0) -> int:
    """Roll back down to ``target`` (default 0); return the new version."""
    version = current_version(db)
    while version > target:
        downs = sorted(_MIGRATIONS_DIR.glob(f"{version:04d}_*.down.sql"))
        if not downs:
            raise MuhideenError(f"no down migration for version {version}")
        with db.write() as conn:
            conn.executescript(downs[0].read_text(encoding="utf-8"))
            conn.execute(_user_version_stmt(version - 1))
        version -= 1
    return version
