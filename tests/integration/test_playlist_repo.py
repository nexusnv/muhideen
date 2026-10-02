"""SqlitePlaylistRepo: playlist aggregate CRUD over the 0002 tables."""

from __future__ import annotations

from pathlib import Path

import pytest

from muhideen.adapters.migrate import migrate
from muhideen.adapters.sqlite_repo import Database
from muhideen.core.errors import ConfigError
from muhideen.core.values import CycleMode, MarkerName, Playlist, PlaylistItem

pytestmark = pytest.mark.integration


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "muhideen.db")
    migrate(database)
    return database


def _item(path: str = "img/a.jpg", duration: int = 10, order: int = 0) -> PlaylistItem:
    return PlaylistItem(image_path=path, duration_s=duration, sort_order=order)


def _playlist(
    pid: str = "p1",
    *,
    title: str | None = None,
    active: bool = True,
    start: str | None = "09:00",
    end: str | None = "11:00",
    anchor: MarkerName | None = None,
    cycle_mode: CycleMode = "indefinite",
    max_cycles: int | None = None,
    items: tuple[PlaylistItem, ...] | None = None,
) -> Playlist:
    return Playlist(
        id=pid,
        title=title if title is not None else pid,
        active=active,
        window_start=start,
        window_end=end,
        anchor_marker=anchor,
        anchor_start_offset_min=0,
        anchor_stop_offset_min=0,
        cycle_mode=cycle_mode,
        max_cycles=max_cycles,
        items=items if items is not None else (_item(),),
    )


def _item_count(db: Database, pid: str) -> int:
    with db.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM playlist_items WHERE playlist_id = ?",
            (pid,),
        ).fetchone()
    return int(row["n"])


def test_save_and_get_round_trips_every_field(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    playlist = Playlist(
        id="ramadan",
        title="Ramadan posters",
        active=False,
        window_start="08:00",
        window_end="maghrib",
        anchor_marker=None,
        anchor_start_offset_min=5,
        anchor_stop_offset_min=-10,
        cycle_mode="repeat",
        max_cycles=3,
        items=(
            _item("img/b.jpg", duration=15, order=1),
            _item("img/a.jpg", duration=10, order=0),
        ),
    )
    repo.save(playlist)
    loaded = repo.get("ramadan")
    assert loaded is not None
    assert [i.sort_order for i in loaded.items] == [0, 1]
    assert loaded == Playlist(
        id="ramadan",
        title="Ramadan posters",
        active=False,
        window_start="08:00",
        window_end="maghrib",
        anchor_marker=None,
        anchor_start_offset_min=5,
        anchor_stop_offset_min=-10,
        cycle_mode="repeat",
        max_cycles=3,
        items=(
            _item("img/a.jpg", duration=10, order=0),
            _item("img/b.jpg", duration=15, order=1),
        ),
    )


def test_get_missing_returns_none(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    assert SqlitePlaylistRepo(_db(tmp_path)).get("nope") is None


def test_list_returns_all_playlists(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    assert repo.list() == []
    repo.save(_playlist("p1"))
    repo.save(_playlist("p2", start=None, end=None))
    assert [p.id for p in repo.list()] == ["p1", "p2"]


def test_save_upserts_fields_and_replaces_items(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1", items=(_item("img/a.jpg"), _item("img/b.jpg"))))
    assert _item_count(db, "p1") == 2
    repo.save(
        _playlist(
            "p1",
            title="renamed",
            active=False,
            items=(_item("img/c.jpg", duration=20),),
        )
    )
    loaded = repo.get("p1")
    assert loaded is not None
    assert (loaded.title, loaded.active) == ("renamed", False)
    assert [i.image_path for i in loaded.items] == ["img/c.jpg"]
    assert _item_count(db, "p1") == 1


def test_save_allows_empty_items(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1", items=()))
    assert repo.get("p1") == _playlist("p1", items=())


def test_anchor_marker_round_trip(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    anchored = Playlist(
        id="anchored",
        title="anchored",
        active=True,
        window_start=None,
        window_end=None,
        anchor_marker=MarkerName.MAGHRIB,
        anchor_start_offset_min=-60,
        anchor_stop_offset_min=30,
        cycle_mode="indefinite",
        max_cycles=None,
        items=(_item(),),
    )
    repo.save(anchored)
    assert repo.get("anchored") == anchored


def test_set_active_toggles(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1"))
    assert repo.set_active("p1", False) is True
    toggled = repo.get("p1")
    assert toggled is not None and toggled.active is False
    assert repo.set_active("p1", True) is True
    restored = repo.get("p1")
    assert restored is not None and restored.active is True


def test_set_active_missing_returns_false(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    assert SqlitePlaylistRepo(_db(tmp_path)).set_active("nope", True) is False


def test_delete_removes_playlist_and_cascades_items(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1", items=(_item("img/a.jpg"), _item("img/b.jpg"))))
    repo.save(_playlist("p2"))
    assert repo.delete("p1") is True
    assert repo.get("p1") is None
    assert _item_count(db, "p1") == 0
    assert repo.get("p2") is not None


def test_delete_missing_returns_false(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    assert SqlitePlaylistRepo(_db(tmp_path)).delete("nope") is False


def test_more_than_50_items_rejected(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(
        _playlist(
            "full",
            items=tuple(_item(f"img/{n}.jpg", order=n) for n in range(50)),
        )
    )
    full = repo.get("full")
    assert full is not None and len(full.items) == 50
    with pytest.raises(ValueError, match="50"):
        repo.save(
            _playlist(
                "over",
                items=tuple(_item(f"img/{n}.jpg", order=n) for n in range(51)),
            )
        )
    assert repo.get("over") is None


def test_corrupt_anchor_marker_raises_config_error(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1"))
    with db.write() as conn:
        conn.execute(
            "UPDATE playlists SET anchor_marker = 'bogus' WHERE id = 'p1'",
        )
    with pytest.raises(ConfigError, match="bogus"):
        repo.get("p1")


def test_corrupt_item_row_raises_config_error(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1"))
    with db.write() as conn:
        conn.execute(
            "UPDATE playlist_items SET duration_s = 0 WHERE playlist_id = 'p1'",
        )
    with pytest.raises(ConfigError):
        repo.get("p1")


def test_corrupt_window_bound_raises_config_error(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    db = _db(tmp_path)
    repo = SqlitePlaylistRepo(db)
    repo.save(_playlist("p1"))
    with db.write() as conn:
        conn.execute(
            "UPDATE playlists SET window_start = 'bogus' WHERE id = 'p1'",
        )
    with pytest.raises(ConfigError, match="bogus"):
        repo.get("p1")


def test_legacy_indefinite_with_max_cycles_loads_as_repeat(tmp_path: Path) -> None:
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo

    # Rows written before the indefinite/repeat split stored `indefinite`
    # with a set max_cycles (which the old stage honored as a bound).
    # Loading must coerce them to `repeat` so behavior is unchanged —
    # never ConfigError (which would poison playlist listing).
    db = _db(tmp_path)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO playlists (id, title, cycle_mode, max_cycles)"
            " VALUES ('legacy', 'Legacy', 'indefinite', 3)",
        )
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, image_path, duration_s,"
            " sort_order) VALUES ('legacy', 'img/a.jpg', 10, 0)",
        )
    loaded = SqlitePlaylistRepo(db).get("legacy")
    assert loaded is not None
    assert loaded.cycle_mode == "repeat"
    assert loaded.max_cycles == 3
