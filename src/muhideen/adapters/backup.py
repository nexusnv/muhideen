"""One-click backup bundle: build/validate/restore (issue #41).

Bundle layout is fixed: ``muhideen.db`` at the zip root plus the uploads
tree under ``media/``. Restore rejects anything else (absolute paths,
``..``, symlinks, non-listed roots) — traversal-safe by construction.
The DB file holds password hashes, so treat every archive as secret.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from muhideen.adapters.migrate import (
    _up_files,  # pyright: ignore[reportPrivateUsage] - same package, stable helper
    _version_of,  # pyright: ignore[reportPrivateUsage] - same package, stable helper
    current_version,
    migrate,
)
from muhideen.adapters.sqlite_repo import Database, backup_to

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
"""Total archive cap (file size and summed member sizes)."""

MAX_MEMBER_BYTES = 64 * 1024 * 1024
"""Per-member uncompressed size cap."""

MAX_MEMBERS = 512
"""Member count cap."""

DB_MEMBER = "muhideen.db"
"""Fixed zip root name for the database snapshot."""

MEDIA_PREFIX = "media/"
"""Fixed zip prefix for every non-database member."""


def _check_name(name: str, info: zipfile.ZipInfo) -> None:
    """Reject one member name/symlink; raise ``ValueError`` when unsafe."""
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ValueError(f"backup member is a symlink: {name!r}")
    if "\\" in name:
        raise ValueError(f"backup member has unsafe name: {name!r}")
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or ".." in parts:
        raise ValueError(f"backup member has unsafe name: {name!r}")
    if name == DB_MEMBER:
        return
    if name == "media/" or name.startswith(MEDIA_PREFIX):
        return
    raise ValueError(f"backup member outside fixed layout: {name!r}")


def _max_known_version() -> int:
    """Highest migration version shipped with this application build."""
    return max((_version_of(path) for path in _up_files()), default=0)


def validate_archive(path: str | Path) -> list[str]:
    """Check caps, layout, and zip integrity; return member names.

    Raises ``ValueError`` for non-zip bytes, oversize archives/members,
    member-count overflow, traversal entries, symlinks, duplicate member
    names, roots outside the fixed layout, and a missing ``muhideen.db``.
    """
    archive = Path(path)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"backup archive exceeds {MAX_ARCHIVE_BYTES} bytes total")
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValueError(f"backup archive exceeds {MAX_MEMBERS} members")
            total = 0
            for info in infos:
                _check_name(info.filename, info)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(
                        f"backup member exceeds {MAX_MEMBER_BYTES} bytes:"
                        f" {info.filename!r}"
                    )
                total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(
                    f"backup contents exceed {MAX_ARCHIVE_BYTES} bytes total"
                )
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("backup archive contains duplicate members")
            if DB_MEMBER not in names:
                raise ValueError("backup archive is missing muhideen.db")
            return names
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid backup archive: {exc}") from exc


def build_backup(db: Database, media_dir: str | Path, dest_zip: str | Path) -> Path:
    """Snapshot ``db`` via ``backup_to`` plus the media tree into a zip."""
    dest = Path(dest_zip)
    media = Path(media_dir)
    with tempfile.TemporaryDirectory(prefix="muhideen-backup-") as tmp:
        snapshot = Path(tmp) / DB_MEMBER
        backup_to(db, snapshot)  # VACUUM INTO: fails if dest exists; ours is fresh
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, DB_MEMBER)
            if media.is_dir():
                for file in sorted(
                    p for p in media.rglob("*") if p.is_file() and not p.is_symlink()
                ):
                    zf.write(file, MEDIA_PREFIX + file.relative_to(media).as_posix())
    return dest


def _swap_media(staged_media: Path, media_dir: Path) -> None:
    """Atomically replace ``media_dir`` with the staged tree (or empty)."""
    staged_media.mkdir(parents=True, exist_ok=True)
    parent = media_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=media_dir.name + ".new-", dir=str(parent)))
    try:
        if any(staged_media.iterdir()):
            for item in staged_media.iterdir():
                if item.is_dir():
                    shutil.copytree(item, pending / item.name)
                else:
                    shutil.copy2(item, pending / item.name)
        if media_dir.is_dir() and not media_dir.is_symlink():
            retired = Path(
                tempfile.mkdtemp(prefix=media_dir.name + ".old-", dir=str(parent))
            )
            retired.rmdir()
            try:
                os.replace(media_dir, retired)
            except OSError:
                shutil.rmtree(pending, ignore_errors=True)
                raise
            try:
                os.replace(pending, media_dir)
            except BaseException:
                os.replace(retired, media_dir)
                raise
            else:
                shutil.rmtree(retired, ignore_errors=True)
        else:
            if media_dir.is_symlink() or media_dir.exists():
                media_dir.unlink()
            os.replace(pending, media_dir)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def restore_backup(
    db: Database,
    media_dir: str | Path,
    staged_zip: str | Path,
    *,
    migrate_fn: Callable[[Database], int] = migrate,
) -> None:
    """Validate, migrate-staged, copy into live, and swap media atomically.

    The staged database is migrated first (a staged ``user_version`` newer
    than any migration shipped here is rejected), then copied into the live
    connection with ``sqlite3.Connection.backup()`` inside ``write()`` —
    no restart. The live database is snapshotted before the copy, so a
    media-swap failure restores the live rows before surfacing the error.
    A staged file that is not a readable database is rejected
    (``ValueError``); live data is untouched on any pre-copy failure. Temp
    staging is removed in ``finally`` (context managers).
    """
    validate_archive(staged_zip)
    target_media = Path(media_dir)
    with tempfile.TemporaryDirectory(prefix="muhideen-restore-") as tmp:
        stage = Path(tmp)
        try:
            with zipfile.ZipFile(staged_zip) as zf:
                try:
                    members = zf.infolist()
                except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                    raise ValueError(f"not a valid backup archive: {exc}") from exc
                for info in members:
                    dest = stage / info.filename
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            payload = zf.read(info.filename)
                        except (
                            zipfile.BadZipFile,
                            zipfile.LargeZipFile,
                        ) as exc:
                            raise ValueError(
                                f"backup member unreadable: {info.filename!r}: {exc}"
                            ) from exc
                        dest.write_bytes(payload)
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ValueError(f"not a valid backup archive: {exc}") from exc
        try:
            staged = Database(stage / DB_MEMBER)
            migrate_fn(staged)
            if current_version(staged) > _max_known_version():
                raise ValueError(
                    "backup from newer application version: "
                    "restore on this build is not supported"
                )
            snapshot = stage / "live-snapshot.db"
            backup_to(db, snapshot)
            try:
                with staged.read() as src, db.write() as live:
                    src.backup(live)
            except (sqlite3.Error, OSError) as exc:
                raise ValueError(f"backup database unreadable: {exc}") from exc
            staged_media = stage / "media"
            try:
                _swap_media(staged_media, target_media)
            except Exception as exc:
                try:
                    rollback = Database(snapshot)
                    with rollback.read() as src, db.write() as live:
                        src.backup(live)
                except Exception:
                    pass
                if isinstance(exc, ValueError):
                    raise
                raise ValueError(f"backup media swap failed: {exc}") from exc
        except (sqlite3.Error, OSError) as exc:
            raise ValueError(f"backup database unreadable: {exc}") from exc
