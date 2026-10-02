"""One-click backup bundle: build/validate/restore round-trip (issue #41)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _seeded_db(tmp_path: Path, name: str = "src.db"):
    from muhideen.adapters.migrate import migrate
    from muhideen.adapters.sqlite_repo import Database

    db = Database(tmp_path / name)
    migrate(db)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("masjid_name", "Backup Mosque"),
        )
    return db


def _source_tree(tmp_path: Path) -> tuple[object, Path]:
    db = _seeded_db(tmp_path)
    media = tmp_path / "src-media"
    (media / "sub").mkdir(parents=True)
    (media / "a.txt").write_text("alpha", encoding="utf-8")
    (media / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return db, media


def test_build_layout_exactness(tmp_path: Path) -> None:
    from muhideen.adapters.backup import build_backup

    db, media = _source_tree(tmp_path)
    dest = tmp_path / "bundle.zip"
    assert build_backup(db, media, dest) == dest
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == [
            "media/a.txt",
            "media/sub/b.txt",
            "muhideen.db",
        ]
        assert zf.read("media/a.txt") == b"alpha"
        assert zf.read("media/sub/b.txt") == b"beta"
        blob = zf.read("muhideen.db")
    assert blob.startswith(b"SQLite format 3")


def _zip_with_names(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"payload")
    return path


def test_validate_rejects_traversal_and_absolute(tmp_path: Path) -> None:
    from muhideen.adapters.backup import validate_archive

    for bad in ("../x", "/abs", "media/../../x", "other/root.txt"):
        archive = _zip_with_names(tmp_path / "bad.zip", ["muhideen.db", bad])
        with pytest.raises(ValueError):
            validate_archive(archive)


def test_validate_rejects_symlink_member(tmp_path: Path) -> None:
    from muhideen.adapters.backup import validate_archive

    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("muhideen.db", b"payload")
        info = zipfile.ZipInfo("media/link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "target")
    with pytest.raises(ValueError, match="symlink"):
        validate_archive(archive)


def test_validate_rejects_oversize_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.backup as backup

    monkeypatch.setattr(backup, "MAX_MEMBER_BYTES", 4)
    archive = _zip_with_names(tmp_path / "big-member.zip", ["muhideen.db"])
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("media/big.bin", b"12345")
    with pytest.raises(ValueError, match="member"):
        backup.validate_archive(archive)


def test_validate_rejects_oversize_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.backup as backup

    db, media = _source_tree(tmp_path)
    archive = tmp_path / "bundle.zip"
    backup.build_backup(db, media, archive)
    monkeypatch.setattr(backup, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="[Tt]otal|archive"):
        backup.validate_archive(archive)


def test_validate_rejects_non_zip_and_missing_db(tmp_path: Path) -> None:
    from muhideen.adapters.backup import validate_archive

    garbage = tmp_path / "garbage.zip"
    garbage.write_bytes(b"not a zip at all")
    with pytest.raises(ValueError):
        validate_archive(garbage)
    missing = _zip_with_names(tmp_path / "missing.zip", ["media/a.txt"])
    with pytest.raises(ValueError, match="muhideen.db"):
        validate_archive(missing)
    trailing = _zip_with_names(
        tmp_path / "trailing.zip", ["muhideen.db/", "media/a.txt"]
    )
    with pytest.raises(ValueError, match="outside fixed layout"):
        validate_archive(trailing)


def test_restore_rejects_unreadable_database_image(tmp_path: Path) -> None:
    from muhideen.adapters.backup import restore_backup
    from muhideen.adapters.sqlite_repo import Database

    live = Database(tmp_path / "live.db")
    live_media = tmp_path / "live-media"
    live_media.mkdir()
    (live_media / "keep.txt").write_text("keep", encoding="utf-8")
    archive = tmp_path / "garbage-db.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("muhideen.db", b"not a database at all")
        zf.writestr("media/a.txt", b"payload")
    with pytest.raises(ValueError, match="unreadable"):
        restore_backup(live, live_media, archive, migrate_fn=lambda staged: 0)
    assert (live_media / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (live_media / "a.txt").exists()


def test_restore_round_trip_with_migrate_stub_and_media_swap(tmp_path: Path) -> None:
    from muhideen.adapters.backup import build_backup, restore_backup
    from muhideen.adapters.sqlite_repo import Database

    db, media = _source_tree(tmp_path)
    archive = tmp_path / "bundle.zip"
    build_backup(db, media, archive)

    live = Database(tmp_path / "live.db")
    from muhideen.adapters.migrate import migrate

    migrate(live)
    live_media = tmp_path / "live-media"
    live_media.mkdir()
    (live_media / "old.txt").write_text("stale", encoding="utf-8")

    calls: list[bool] = []

    def fake_migrate(staged: Database) -> int:
        calls.append(True)
        with staged.read() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'masjid_name'"
            ).fetchone()
            assert row is not None and row[0] == "Backup Mosque"
        return 0

    restore_backup(live, live_media, archive, migrate_fn=fake_migrate)
    assert calls == [True]
    with live.read() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'masjid_name'"
        ).fetchone()
        assert row is not None and row[0] == "Backup Mosque"
    assert (live_media / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (live_media / "sub" / "b.txt").read_text(encoding="utf-8") == "beta"
    assert not (live_media / "old.txt").exists()


def test_restore_cleans_up_tmp_staging(tmp_path: Path) -> None:
    from muhideen.adapters.backup import build_backup, restore_backup
    from muhideen.adapters.sqlite_repo import Database

    db, media = _source_tree(tmp_path)
    archive = tmp_path / "bundle.zip"
    build_backup(db, media, archive)

    live = Database(tmp_path / "live.db")
    live_media = tmp_path / "live-media"
    live_media.mkdir()
    restore_backup(live, live_media, archive, migrate_fn=lambda staged: 0)
    assert not any(
        ".new-" in p.name or ".old-" in p.name or "muhideen-restore-" in p.name
        for p in tmp_path.iterdir()
    )
