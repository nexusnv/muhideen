"""SqliteUserRepo: Argon2id single-admin store (slice 1A-7, Task 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database, SqliteUserRepo

pytestmark = pytest.mark.integration


def _repo(tmp_path: Path) -> tuple[Database, SqliteUserRepo]:
    db = Database(tmp_path / "muhideen.db")
    migrate(db)
    return db, SqliteUserRepo(db)


def test_has_users_is_false_initially(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    assert repo.has_users() is False


def test_create_returns_true_first_time(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    assert repo.create_user("admin", "password123") is True
    assert repo.has_users() is True


def test_second_create_is_false(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    assert repo.create_user("admin", "password123") is True
    assert repo.create_user("admin", "otherpass456") is False


def test_verify_correct_password_is_true(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    repo.create_user("admin", "password123")
    assert repo.verify("admin", "password123") is True


def test_verify_wrong_password_is_false(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    repo.create_user("admin", "password123")
    assert repo.verify("admin", "wrongpass999") is False


def test_verify_unknown_user_is_false(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    assert repo.verify("admin", "password123") is False


def test_stored_hash_uses_argon2id(tmp_path: Path) -> None:
    db, repo = _repo(tmp_path)
    repo.create_user("admin", "password123")
    with db.read() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", ("admin",)
        ).fetchone()
    assert row is not None
    assert str(row["password_hash"]).startswith("$argon2id$")
