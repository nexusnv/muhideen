"""Playlist persistence: the playlist aggregate over the 0002 tables.

``SqlitePlaylistRepo`` follows the ``sqlite_repo`` pattern: the shared
single-writer :class:`Database` handle, short transactions, rows mapped
back to the ``core.values`` playlist value objects. Window strings are
stored verbatim — window math lives in ``domain``. Corrupt rows surface
as ``ConfigError``, mirroring the settings repo boundary.
"""

from __future__ import annotations

import sqlite3

from muhideen.adapters.sqlite_repo import Database
from muhideen.core.errors import ConfigError
from muhideen.core.values import MarkerName, Playlist, PlaylistItem
from muhideen.domain.playlist_window import parse_window

MAX_PLAYLIST_ITEMS = 50
"""Per-playlist item cap: larger saves are rejected before touching the DB."""


def _parse_anchor(raw: str | None) -> MarkerName | None:
    """Map a stored anchor marker; None stays open, unknown names fail."""
    if raw is None:
        return None
    try:
        return MarkerName(raw)
    except ValueError:
        raise ConfigError(f"unknown playlist anchor marker: {raw!r}") from None


def _item_from_row(row: sqlite3.Row) -> PlaylistItem:
    """Map one playlist_items row; corrupt values become ``ConfigError``."""
    try:
        return PlaylistItem(
            image_path=row["image_path"],
            duration_s=row["duration_s"],
            sort_order=row["sort_order"],
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _playlist_from_rows(meta: sqlite3.Row, item_rows: list[sqlite3.Row]) -> Playlist:
    """Map one playlists row plus its ordered items to the value object."""
    # Pre-pairing rows (written before the indefinite/repeat split) stored
    # `indefinite` with a set max_cycles, which the old stage honored as a
    # bound. Coerce them to `repeat` on load so their observable behavior
    # is unchanged; any later save persists the coerced mode.
    mode = meta["cycle_mode"]
    if mode == "indefinite" and meta["max_cycles"] is not None:
        mode = "repeat"
    try:
        playlist = Playlist(
            id=meta["id"],
            title=meta["title"],
            active=bool(meta["active"]),
            window_start=meta["window_start"],
            window_end=meta["window_end"],
            anchor_marker=_parse_anchor(meta["anchor_marker"]),
            anchor_start_offset_min=meta["anchor_start_offset_min"],
            anchor_stop_offset_min=meta["anchor_stop_offset_min"],
            cycle_mode=mode,
            max_cycles=meta["max_cycles"],
            items=tuple(_item_from_row(row) for row in item_rows),
        )
        parse_window(playlist)
        return playlist
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


class SqlitePlaylistRepo:
    """Playlist aggregate CRUD: upsert replaces the full item set."""

    def __init__(self, db: Database) -> None:
        """Hold the shared single-writer database handle."""
        self._db = db

    def list(self) -> list[Playlist]:
        """Return every playlist with items ordered by sort order."""
        with self._db.read() as conn:
            metas = conn.execute("SELECT * FROM playlists ORDER BY rowid").fetchall()
            item_rows = conn.execute(
                "SELECT * FROM playlist_items ORDER BY playlist_id, sort_order, id"
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in item_rows:
            grouped.setdefault(str(row["playlist_id"]), []).append(row)
        return [
            _playlist_from_rows(meta, grouped.get(str(meta["id"]), []))
            for meta in metas
        ]

    def get(self, playlist_id: str) -> Playlist | None:
        """Return one playlist with ordered items, else None."""
        with self._db.read() as conn:
            meta = conn.execute(
                "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
            if meta is None:
                return None
            item_rows = conn.execute(
                "SELECT * FROM playlist_items WHERE playlist_id = ?"
                " ORDER BY sort_order, id",
                (playlist_id,),
            ).fetchall()
        return _playlist_from_rows(meta, item_rows)

    def save(self, playlist: Playlist) -> None:
        """Upsert a playlist and replace its item set in one transaction."""
        if len(playlist.items) > MAX_PLAYLIST_ITEMS:
            raise ValueError(
                f"playlist {playlist.id!r} exceeds"
                f" {MAX_PLAYLIST_ITEMS} items: {len(playlist.items)}"
            )
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO playlists (id, title, active, window_start,"
                " window_end, anchor_marker, anchor_start_offset_min,"
                " anchor_stop_offset_min, cycle_mode, max_cycles)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                " active=excluded.active, window_start=excluded.window_start,"
                " window_end=excluded.window_end,"
                " anchor_marker=excluded.anchor_marker,"
                " anchor_start_offset_min=excluded.anchor_start_offset_min,"
                " anchor_stop_offset_min=excluded.anchor_stop_offset_min,"
                " cycle_mode=excluded.cycle_mode, max_cycles=excluded.max_cycles",
                (
                    playlist.id,
                    playlist.title,
                    1 if playlist.active else 0,
                    playlist.window_start,
                    playlist.window_end,
                    playlist.anchor_marker.value
                    if playlist.anchor_marker is not None
                    else None,
                    playlist.anchor_start_offset_min,
                    playlist.anchor_stop_offset_min,
                    playlist.cycle_mode,
                    playlist.max_cycles,
                ),
            )
            conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id = ?", (playlist.id,)
            )
            conn.executemany(
                "INSERT INTO playlist_items"
                " (playlist_id, image_path, duration_s, sort_order)"
                " VALUES (?,?,?,?)",
                [
                    (playlist.id, item.image_path, item.duration_s, item.sort_order)
                    for item in playlist.items
                ],
            )

    def append_item(self, playlist_id: str, item: PlaylistItem) -> tuple[Playlist, int]:
        """Append one item atomically; read+write in one transaction.

        Returns the refreshed playlist plus the assigned sort order.
        Raises ``KeyError`` when the playlist is unknown, ``ValueError``
        when the item cap is reached. The single ``write()`` lock covers
        the item-count check and the insert, so concurrent uploads cannot
        both pass the cap or lose each other's rows.
        """
        with self._db.write() as conn:
            meta = conn.execute(
                "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
            if meta is None:
                raise KeyError(f"unknown playlist: {playlist_id!r}")
            count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM playlist_items WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            count = int(count_row["n"]) if count_row is not None else 0
            if count >= MAX_PLAYLIST_ITEMS:
                raise ValueError(
                    f"playlist {playlist_id!r} exceeds"
                    f" {MAX_PLAYLIST_ITEMS} items: {count}"
                )
            max_row = conn.execute(
                "SELECT MAX(sort_order) AS m FROM playlist_items WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            order = (
                int(max_row["m"]) + 1
                if max_row is not None and max_row["m"] is not None
                else 0
            )
            conn.execute(
                "INSERT INTO playlist_items"
                " (playlist_id, image_path, duration_s, sort_order)"
                " VALUES (?,?,?,?)",
                (playlist_id, item.image_path, item.duration_s, order),
            )
            item_rows = conn.execute(
                "SELECT * FROM playlist_items WHERE playlist_id = ?"
                " ORDER BY sort_order, id",
                (playlist_id,),
            ).fetchall()
            meta_fresh = conn.execute(
                "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
        assert meta_fresh is not None
        return _playlist_from_rows(meta_fresh, list(item_rows)), order

    def remove_item(self, playlist_id: str, sort_order: int) -> bool:
        """Remove the item at one sort position atomically.

        Returns False when the playlist or the position is unknown.
        The delete runs in one transaction, so a concurrent upload
        cannot resurrect a deleted row or lose an appended one.
        """
        with self._db.write() as conn:
            meta = conn.execute(
                "SELECT id FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
            if meta is None:
                return False
            cursor = conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND sort_order = ?",
                (playlist_id, sort_order),
            )
            return cursor.rowcount > 0

    def delete(self, playlist_id: str) -> bool:
        """Delete a playlist; its items cascade. False when missing."""
        with self._db.write() as conn:
            cursor = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        return cursor.rowcount > 0

    def set_active(self, playlist_id: str, active: bool) -> bool:
        """Toggle a playlist; False when the id is unknown."""
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE playlists SET active = ? WHERE id = ?",
                (1 if active else 0, playlist_id),
            )
        return cursor.rowcount > 0
