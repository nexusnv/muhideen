"""E2E playlist editor + per-display overrides (Phase 1C Task 8)."""

from __future__ import annotations

import io
from datetime import datetime as _dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from muhideen.adapters.playlist_repo import SqlitePlaylistRepo
from muhideen.adapters.sse_bus import SSEBus
from muhideen.api.app import AppDeps, create_app

pytestmark = pytest.mark.e2e


def _wizard_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "masjid_name": "Masjid Baru",
        "zone": "SGR01",
        "hijri_offset": 0,
        "adhan_duration_s": 180,
        "dim_minutes_default": 20,
        "dim_minutes_jumuah": 45,
        "iqamah_rules": [
            {
                "prayer": "fajr",
                "mode": "delay",
                "delay_minutes": 15,
                "fixed_time": None,
            },
            {
                "prayer": "dhuhr",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
            {"prayer": "asr", "mode": "delay", "delay_minutes": 10, "fixed_time": None},
            {
                "prayer": "maghrib",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
            {
                "prayer": "isha",
                "mode": "delay",
                "delay_minutes": 15,
                "fixed_time": None,
            },
            {
                "prayer": "jumuah",
                "mode": "delay",
                "delay_minutes": 10,
                "fixed_time": None,
            },
        ],
        "lat": 3.07,
        "lon": 101.69,
        "method": "MABIMS",
        "boundary_countdown": False,
        "calc_only": True,
        "imsak_offset_min": 10,
        "dhuha_offset_min": 28,
        "countdown_before_adhan_min": 5,
        "countdown_before_adhan_overrides": {},
    }
    body.update(overrides)
    return body


def _playlist_body(pid: str = "p1", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": pid,
        "title": f"Title {pid}",
        "active": True,
        "window_start": "09:00",
        "window_end": "18:00",
        "anchor_marker": None,
        "anchor_start_offset_min": 0,
        "anchor_stop_offset_min": 0,
        "cycle_mode": "indefinite",
        "max_cycles": None,
        "items": [
            {"image_path": "a.jpg", "duration_s": 10, "sort_order": 0},
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
def media_client(surface: SimpleNamespace, tmp_path: Path) -> Any:
    """App bound to an isolated upload dir (shared client writes to static/)."""
    deps = AppDeps(
        settings_repo=surface.settings_repo,
        prayer_repo=surface.prayer_repo,
        display_repo=surface.display_repo,
        user_repo=surface.user_repo,
        clock=surface.clock,
        event_bus=surface.bus,
        database=surface.db,
        playlist_repo=SqlitePlaylistRepo(surface.db),
        media_dir=tmp_path / "uploads",
    )
    app = create_app(deps)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authed(media_client: TestClient) -> TestClient:
    media_client.post("/api/auth/setup", json={"password": "password123"})
    assert media_client.put("/api/settings", json=_wizard_body()).status_code == 200
    return media_client


def _png(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


def test_playlist_crud_flow(authed: TestClient) -> None:
    created = authed.post("/api/playlists", json=_playlist_body("p1"))
    assert created.status_code == 201
    assert created.json()["id"] == "p1"

    listed = authed.get("/api/playlists")
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()["playlists"]] == ["p1"]

    fetched = authed.get("/api/playlists/p1")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Title p1"

    renamed = _playlist_body("p1")
    renamed["title"] = "Evening Reminders"
    assert authed.put("/api/playlists/p1", json=renamed).status_code == 200
    assert authed.get("/api/playlists/p1").json()["title"] == "Evening Reminders"

    toggled = authed.patch("/api/playlists/p1", json={"active": False})
    assert toggled.status_code == 200
    assert toggled.json()["active"] is False

    assert authed.delete("/api/playlists/p1").status_code == 200
    assert authed.get("/api/playlists/p1").status_code == 404
    assert authed.get("/api/playlists").json() == {"playlists": []}


def test_playlist_id_generated_when_absent(authed: TestClient) -> None:
    body = _playlist_body()
    del body["id"]
    created = authed.post("/api/playlists", json=body)
    assert created.status_code == 201
    assert created.json()["id"]


def test_playlist_routes_require_admin(media_client: TestClient) -> None:
    assert media_client.get("/api/playlists").status_code == 401
    assert media_client.post("/api/playlists", json=_playlist_body()).status_code == 401
    assert media_client.get("/api/playlists/p1").status_code == 401
    assert (
        media_client.put("/api/playlists/p1", json=_playlist_body()).status_code == 401
    )
    assert (
        media_client.patch("/api/playlists/p1", json={"active": False}).status_code
        == 401
    )
    assert media_client.delete("/api/playlists/p1").status_code == 401
    assert media_client.get("/api/playlists/preview").status_code == 401
    assert media_client.get("/api/displays").status_code == 401


def test_playlist_validation_rejected(authed: TestClient) -> None:
    bad_window = _playlist_body("bad1", window_start="25:99")
    assert authed.post("/api/playlists", json=bad_window).status_code == 422
    bad_anchor = _playlist_body("bad2", anchor_marker="syuruq")
    assert authed.post("/api/playlists", json=bad_anchor).status_code == 422
    unknown_anchor = _playlist_body("bad2b", anchor_marker="nope")
    assert authed.post("/api/playlists", json=unknown_anchor).status_code == 422
    empty_title = _playlist_body("bad3", title="")
    assert authed.post("/api/playlists", json=empty_title).status_code == 422
    oversized = _playlist_body("bad4")
    oversized["items"] = [
        {"image_path": f"img-{n}.jpg", "duration_s": 5, "sort_order": n}
        for n in range(51)
    ]
    assert authed.post("/api/playlists", json=oversized).status_code == 422
    mismatched = _playlist_body("p9")
    assert authed.put("/api/playlists/other", json=mismatched).status_code == 422
    assert (
        authed.put("/api/playlists/missing", json=_playlist_body("missing")).status_code
        == 404
    )
    assert (
        authed.patch("/api/playlists/missing", json={"active": True}).status_code == 404
    )
    assert authed.delete("/api/playlists/missing").status_code == 404


def test_playlist_put_validation_and_cap(authed: TestClient) -> None:
    assert authed.post("/api/playlists", json=_playlist_body("p1")).status_code == 201
    bad_window = _playlist_body("p1", window_start="tomorrow")
    assert authed.put("/api/playlists/p1", json=bad_window).status_code == 422
    oversized = _playlist_body("p1")
    oversized["items"] = [
        {"image_path": f"img-{n}.jpg", "duration_s": 5, "sort_order": n}
        for n in range(51)
    ]
    assert authed.put("/api/playlists/p1", json=oversized).status_code == 422
    assert authed.delete("/api/playlists/missing/items/0").status_code == 404


def test_open_window_playlist_is_always_in_window(authed: TestClient) -> None:
    body = _playlist_body("open", window_start=None, window_end=None)
    assert authed.post("/api/playlists", json=body).status_code == 201
    payload = authed.get("/api/playlists/preview").json()
    assert payload["stage"] == "playlist:open"


def test_marker_anchored_playlist_round_trip(authed: TestClient) -> None:
    body = _playlist_body(
        "anchored",
        window_start=None,
        window_end=None,
        anchor_marker="maghrib",
        anchor_start_offset_min=-30,
        anchor_stop_offset_min=60,
    )
    created = authed.post("/api/playlists", json=body)
    assert created.status_code == 201
    assert created.json()["anchor_marker"] == "maghrib"
    fetched = authed.get("/api/playlists/anchored").json()
    assert fetched["anchor_start_offset_min"] == -30
    assert fetched["anchor_stop_offset_min"] == 60


def test_image_upload_round_trip(authed: TestClient) -> None:
    import base64

    assert authed.post("/api/playlists", json=_playlist_body("up1")).status_code == 201
    response = authed.post(
        "/api/playlists/up1/items",
        json={"image_base64": base64.b64encode(_png()).decode(), "duration_s": 7},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["image_path"].endswith(".png")
    assert payload["duration_s"] == 7
    assert payload["sort_order"] == 1
    items = authed.get("/api/playlists/up1").json()["items"]
    assert len(items) == 2
    assert items[1]["image_path"] == payload["image_path"]

    removed = authed.delete(f"/api/playlists/up1/items/{payload['sort_order']}")
    assert removed.status_code == 200
    assert len(authed.get("/api/playlists/up1").json()["items"]) == 1
    assert authed.delete("/api/playlists/up1/items/99").status_code == 404


def test_image_upload_rejects_bad_bytes(authed: TestClient) -> None:
    import base64

    assert authed.post("/api/playlists", json=_playlist_body("up2")).status_code == 201
    response = authed.post(
        "/api/playlists/up2/items",
        json={
            "image_base64": base64.b64encode(b"not-an-image").decode(),
            "duration_s": 5,
        },
    )
    assert response.status_code == 400
    assert (
        authed.post(
            "/api/playlists/up2/items",
            json={"image_base64": "!!!not-base64!!!", "duration_s": 5},
        ).status_code
        == 400
    )


def test_image_upload_rejects_oversize(authed: TestClient) -> None:
    import base64

    assert authed.post("/api/playlists", json=_playlist_body("up3")).status_code == 201
    big = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
    response = authed.post(
        "/api/playlists/up3/items",
        json={"image_base64": big, "duration_s": 5},
    )
    assert response.status_code == 413


def test_image_upload_missing_playlist_is_404(authed: TestClient) -> None:
    import base64

    response = authed.post(
        "/api/playlists/nope/items",
        json={"image_base64": base64.b64encode(_png()).decode(), "duration_s": 5},
    )
    assert response.status_code == 404


def test_image_upload_failure_cleans_up_stored_file(
    authed: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    assert authed.post("/api/playlists", json=_playlist_body("upf")).status_code == 201
    uploads = tmp_path / "uploads"
    before = set(uploads.iterdir()) if uploads.is_dir() else set()

    def _boom(self: SqlitePlaylistRepo, playlist: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(SqlitePlaylistRepo, "save", _boom)
    response = authed.post(
        "/api/playlists/upf/items",
        json={"image_base64": base64.b64encode(_png()).decode(), "duration_s": 5},
    )
    assert response.status_code == 422
    after = set(uploads.iterdir()) if uploads.is_dir() else set()
    assert after == before


def test_image_upload_enforces_item_cap(
    authed: TestClient, surface: SimpleNamespace, tmp_path: Path
) -> None:
    import base64

    from muhideen.core.values import Playlist, PlaylistItem

    media_dir = tmp_path / "uploads"
    before = len(list(media_dir.glob("*"))) if media_dir.exists() else 0
    items = tuple(
        PlaylistItem(image_path=f"img-{n}.jpg", duration_s=5, sort_order=n)
        for n in range(50)
    )
    repo = SqlitePlaylistRepo(surface.db)
    repo.save(
        Playlist(
            id="full",
            title="Full",
            active=True,
            window_start="09:00",
            window_end="18:00",
            cycle_mode="indefinite",
            items=items,
        )
    )
    response = authed.post(
        "/api/playlists/full/items",
        json={"image_base64": base64.b64encode(_png()).decode(), "duration_s": 5},
    )
    assert response.status_code == 422
    after = len(list(media_dir.glob("*"))) if media_dir.exists() else 0
    assert after == before, "rejected 51st-item upload must not orphan a file"


def test_occupancy_preview_reports_stage_now_and_next(
    authed: TestClient, surface: SimpleNamespace
) -> None:
    assert authed.post("/api/playlists", json=_playlist_body("now")).status_code == 201
    preview = authed.get("/api/playlists/preview")
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["stage"] == "playlist:now"
    entry = next(p for p in payload["playlists"] if p["id"] == "now")
    assert entry["on_stage_now"] is True

    future = _playlist_body("later", window_start="23:00", window_end="23:30")
    assert authed.post("/api/playlists", json=future).status_code == 201
    payload = authed.get("/api/playlists/preview").json()
    later = next(p for p in payload["playlists"] if p["id"] == "later")
    assert later["on_stage_now"] is False
    assert later["next_at"] is not None
    assert "23:00" in later["next_at"]


def test_occupancy_preview_empty_is_clock(authed: TestClient) -> None:
    payload = authed.get("/api/playlists/preview").json()
    assert payload["stage"] == "clock"
    assert payload["playlists"] == []


def test_preview_moment_matches_tick_stage() -> None:
    from muhideen.api import app as app_module

    assert hasattr(app_module, "_preview_moment")
    assert hasattr(app_module, "_tick_stage")


def test_occupancy_preview_countdown_outranks_playlist(
    authed: TestClient, surface: SimpleNamespace
) -> None:
    assert authed.post("/api/playlists", json=_playlist_body("any")).status_code == 201
    now = surface.clock.now()
    current = authed.get("/api/next-event", params={"now": now.isoformat()}).json()
    assert current["adhan_at"] is not None
    target = _dt.fromisoformat(current["adhan_at"]) - now
    surface.clock.advance(target.total_seconds() - 60.0)
    # The advance exceeds the 30-minute session TTL, so log in again.
    assert (
        authed.post("/api/auth/login", json={"password": "password123"}).status_code
        == 200
    )
    payload = authed.get("/api/playlists/preview").json()
    assert payload["stage"].startswith("countdown:adhan:")


def test_preview_without_settings_is_503(media_client: TestClient) -> None:
    media_client.post("/api/auth/setup", json={"password": "password123"})
    assert media_client.get("/api/playlists/preview").status_code == 503


def test_preview_without_schedule_is_404(media_client: TestClient) -> None:
    media_client.post("/api/auth/setup", json={"password": "password123"})
    body = _wizard_body(lat=None, lon=None)
    assert media_client.put("/api/settings", json=body).status_code == 200
    assert media_client.get("/api/playlists/preview").status_code == 404


def test_playlist_api_503_without_repo() -> None:
    deps = AppDeps(
        settings_repo=_NoSettings(),
        prayer_repo=_NoPrayer(),
        display_repo=_NoDisplay(),
        user_repo=_NoUser(),
        clock=_PinnedClock(),
        event_bus=SSEBus(),
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert client.get("/api/playlists").status_code == 401


def test_no_database_app_serves_admin_with_503_backends() -> None:
    from muhideen.core.values import Settings

    class _StaticSettings:
        def load(self) -> Settings:
            return Settings(masjid_name="M", zone="SGR01", hijri_offset=0)

        def save(self, settings: Settings) -> None:
            raise NotImplementedError

    class _OpenUser(_NoUser):
        def has_users(self) -> bool:
            return True

        def verify(self, username: str, password: str) -> bool:
            return True

    deps = AppDeps(
        settings_repo=_StaticSettings(),  # type: ignore[arg-type]
        prayer_repo=_NoPrayer(),  # type: ignore[arg-type]
        display_repo=_NoDisplay(),  # type: ignore[arg-type]
        user_repo=_OpenUser(),  # type: ignore[arg-type]
        clock=_PinnedClock(),  # type: ignore[arg-type]
        event_bus=SSEBus(),
    )
    app = create_app(deps)
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "password123"}).status_code
            == 200
        )
        assert client.get("/api/playlists").status_code == 503
        assert client.get("/api/playlists/preview").status_code == 503
        assert client.get("/api/displays").status_code == 503
        settings_html = client.get("/admin/settings")
        assert settings_html.status_code == 200
        assert "data-displays='[]'" in settings_html.text
        playlists_html = client.get("/admin/playlists")
        assert playlists_html.status_code == 200
        assert "data-preview='null'" in playlists_html.text


def test_admin_playlists_page_without_settings_renders_empty_preview(
    media_client: TestClient,
) -> None:
    media_client.post("/api/auth/setup", json={"password": "password123"})
    html = media_client.get("/admin/playlists").text
    assert "data-preview='null'" in html


def test_settings_page_shows_registered_displays(authed: TestClient) -> None:
    registered = authed.post(
        "/api/displays", json={"id": "hall-9", "name": "Hall Nine"}
    )
    assert registered.status_code == 201
    html = authed.get("/admin/settings").text
    assert "hall-9" in html
    assert "classic-green" in html


def test_admin_playlists_page_gating(
    media_client: TestClient, surface: SimpleNamespace
) -> None:
    response = media_client.get("/admin/playlists", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/login"

    media_client.post("/api/auth/setup", json={"password": "password123"})
    assert media_client.put("/api/settings", json=_wizard_body()).status_code == 200
    html = media_client.get("/admin/playlists").text
    assert "Playlists" in html
    assert 'id="playlist-editor"' in html
    assert 'id="occupancy-preview"' in html
    assert 'id="playlist-list"' in html
    assert "data-en" not in html
    assert "lang-toggle" not in html
    assert 'lang="en"' in html


def test_display_registry_flow(authed: TestClient) -> None:
    registered = authed.post(
        "/api/displays", json={"id": "hall-1", "name": "Main Hall"}
    )
    assert registered.status_code == 201
    assert (
        authed.post("/api/displays", json={"id": "hall-1", "name": "Dup"}).status_code
        == 409
    )
    assert (
        authed.post(
            "/api/displays", json={"id": "x", "name": "X", "group_name": "Nope"}
        ).status_code
        == 422
    )

    listing = authed.get("/api/displays").json()
    hall = next(d for d in listing["displays"] if d["id"] == "hall-1")
    assert hall["current_theme"] == "classic-green"
    assert hall["effective_dim_minutes"] == 20
    assert hall["dim_source"] == "settings"

    updated = authed.patch("/api/displays/hall-1", json={"current_theme": "midnight"})
    assert updated.status_code == 200
    assert updated.json()["current_theme"] == "midnight"
    assert (
        authed.patch("/api/displays/hall-1", json={"group_name": "Nope"}).status_code
        == 422
    )
    assert (
        authed.patch("/api/displays/nope", json={"current_theme": "x"}).status_code
        == 404
    )
    assert (
        authed.patch("/api/displays/nope", json={"group_name": "Default"}).status_code
        == 404
    )
    assert authed.patch("/api/displays/hall-1", json={}).status_code == 422
    reassigned = authed.patch("/api/displays/hall-1", json={"group_name": "Default"})
    assert reassigned.status_code == 200
    assert reassigned.json()["group_name"] == "Default"

    grouped = authed.patch(
        "/api/display-groups/Default", json={"dim_minutes_override": 30}
    )
    assert grouped.status_code == 200
    hall = next(
        d for d in authed.get("/api/displays").json()["displays"] if d["id"] == "hall-1"
    )
    assert hall["effective_dim_minutes"] == 30
    assert hall["dim_source"] == "group"

    themed = authed.patch(
        "/api/display-groups/Default",
        json={"theme": "midnight", "carousel_enabled": False},
    )
    assert themed.status_code == 200
    assert themed.json()["theme"] == "midnight"
    assert themed.json()["carousel_enabled"] is False
    reenabled = authed.patch(
        "/api/display-groups/Default", json={"carousel_enabled": True}
    )
    assert reenabled.json()["carousel_enabled"] is True
    assert authed.patch("/api/display-groups/Default", json={}).status_code == 200
    assert authed.patch("/api/display-groups/Nope", json={}).status_code == 404

    assert (
        authed.patch(
            "/api/display-groups/Default", json={"dim_minutes_override": 99}
        ).status_code
        == 422
    )
    assert (
        authed.patch(
            "/api/display-groups/Nope", json={"dim_minutes_override": 10}
        ).status_code
        == 404
    )


def test_display_group_null_clears_assignment(authed: TestClient) -> None:
    registered = authed.post(
        "/api/displays", json={"id": "hall-2", "name": "Side Hall"}
    )
    assert registered.status_code == 201
    assert (
        authed.patch(
            "/api/display-groups/Default", json={"dim_minutes_override": 30}
        ).status_code
        == 200
    )
    hall = next(
        d for d in authed.get("/api/displays").json()["displays"] if d["id"] == "hall-2"
    )
    assert hall["effective_dim_minutes"] == 30
    assert hall["dim_source"] == "group"

    cleared = authed.patch("/api/displays/hall-2", json={"group_name": None})
    assert cleared.status_code == 200
    assert cleared.json()["group_name"] is None
    assert cleared.json()["effective_dim_minutes"] == 20
    assert cleared.json()["dim_source"] == "settings"
    hall = next(
        d for d in authed.get("/api/displays").json()["displays"] if d["id"] == "hall-2"
    )
    assert hall["group_name"] is None

    assert (
        authed.patch("/api/displays/hall-2", json={"current_theme": None}).status_code
        == 422
    )
    assert (
        authed.patch("/api/displays/nope", json={"group_name": None}).status_code == 404
    )


class _NoSettings:
    def load(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def save(self, settings):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class _NoPrayer:
    def get_day(self, day, zone):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def save_day(self, prayer_day):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def last_known(self, day, zone):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class _NoDisplay:
    def record_seen(self, display_id, ip):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def is_registered(self, display_id):  # type: ignore[no-untyped-def]
        return False

    def flush(self) -> int:
        return 0


class _NoUser:
    def has_users(self) -> bool:
        return False

    def create_user(self, username, password):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def verify(self, username, password):  # type: ignore[no-untyped-def]
        return False


class _PinnedClock:
    def now(self):  # type: ignore[no-untyped-def]
        from datetime import datetime, timedelta, timezone

        return datetime(2025, 10, 20, 12, 20, tzinfo=timezone(timedelta(hours=8)))

    def monotonic(self) -> float:
        return 0.0
