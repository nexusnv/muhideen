"""E2E backup export/restore + service logs (issue #41, Task 3)."""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from muhideen.adapters.playlist_repo import SqlitePlaylistRepo
from muhideen.api.app import AppDeps, BackupRestoreDTO, create_app

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"


@pytest.fixture
def media_client(surface: SimpleNamespace, tmp_path: Path) -> Any:
    """App bound to an isolated upload dir (mirrors test_admin_playlists)."""
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
    payload: dict[str, Any] = json.loads((FIXTURES / "settings.json").read_text())
    assert media_client.put("/api/settings", json=payload).status_code == 200
    return media_client


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _zip_bytes(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"payload")
    return buf.getvalue()


def test_backup_routes_require_admin(media_client: TestClient) -> None:
    assert media_client.post("/api/backup/export").status_code == 401
    assert (
        media_client.post(
            "/api/backup/restore", json={"archive_base64": _b64(b"x")}
        ).status_code
        == 401
    )
    assert media_client.get("/api/logs").status_code == 401


def test_export_download_round_trips_through_restore(
    authed: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = authed.get("/api/settings").json()
    assert before["masjid_name"]
    media = tmp_path / "uploads"
    media.mkdir(parents=True, exist_ok=True)
    (media / "note.txt").write_bytes(b"keep me")

    import tempfile

    export_tmps: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def _recording_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        export_tmps.append(name)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", _recording_mkstemp)
    exported = authed.post("/api/backup/export")
    assert exported.status_code == 200
    assert export_tmps, "export must stage exactly one temp zip"
    for name in export_tmps:
        assert not Path(name).exists(), f"export temp leaked: {name}"
    assert exported.headers["content-type"] == "application/zip"
    disposition = exported.headers["content-disposition"]
    assert re.search(r"muhideen-backup-.*\.zip", disposition), disposition
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        assert "muhideen.db" in zf.namelist()
        assert zf.read("media/note.txt") == b"keep me"

    renamed = dict(before, masjid_name="Masjid Changed")
    assert authed.put("/api/settings", json=renamed).status_code == 200
    (media / "note.txt").unlink()

    restored = authed.post(
        "/api/backup/restore", json={"archive_base64": _b64(exported.content)}
    )
    assert restored.status_code == 200
    assert restored.json() == {"ok": True}
    assert authed.get("/api/settings").json()["masjid_name"] == before["masjid_name"]
    assert (media / "note.txt").read_bytes() == b"keep me"


def test_restore_rejects_corrupt_archives(authed: TestClient) -> None:
    assert (
        authed.post(
            "/api/backup/restore", json={"archive_base64": "!!!not-base64!!!"}
        ).status_code
        == 400
    )
    assert (
        authed.post(
            "/api/backup/restore", json={"archive_base64": _b64(b"not a zip")}
        ).status_code
        == 400
    )
    traversal = _b64(_zip_bytes(["muhideen.db", "../evil.txt"]))
    assert (
        authed.post(
            "/api/backup/restore", json={"archive_base64": traversal}
        ).status_code
        == 400
    )
    missing_db = _b64(_zip_bytes(["media/only.txt"]))
    assert (
        authed.post(
            "/api/backup/restore", json={"archive_base64": missing_db}
        ).status_code
        == 400
    )
    garbage_db = io.BytesIO()
    with zipfile.ZipFile(garbage_db, "w") as zf:
        zf.writestr("muhideen.db", b"not a database at all")
        zf.writestr("media/a.txt", b"payload")
    assert (
        authed.post(
            "/api/backup/restore",
            json={"archive_base64": _b64(garbage_db.getvalue())},
        ).status_code
        == 400
    )


def test_restore_rejects_oversize_base64_before_decode(
    authed: TestClient,
) -> None:
    from muhideen.adapters.backup import MAX_ARCHIVE_BYTES

    bound = (MAX_ARCHIVE_BYTES + 2) // 3 * 4 + 4
    oversize = "A" * (bound + 100)
    assert (
        authed.post(
            "/api/backup/restore", json={"archive_base64": oversize}
        ).status_code
        == 413
    )


def test_logs_available_shape(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.logs as logs_adapter

    seen: dict[str, list[str]] = {}

    def _fake(cmd: list[str]) -> str:
        seen["cmd"] = cmd
        return "\n".join(
            json.dumps({"MESSAGE": line}) for line in ("line1", "line2", "line3")
        )

    monkeypatch.setattr(logs_adapter, "_run", _fake)
    response = authed.get("/api/logs")
    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "lines": ["line1", "line2", "line3"],
    }
    assert seen["cmd"][-2:] == ["-n", "100"]


def test_logs_lines_param_reaches_runner(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.logs as logs_adapter

    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        logs_adapter,
        "_run",
        lambda cmd: seen.update(cmd=cmd) or json.dumps({"MESSAGE": "only"}),
    )
    assert authed.get("/api/logs", params={"lines": 5}).json() == {
        "available": True,
        "lines": ["only"],
    }
    assert seen["cmd"][-2:] == ["-n", "5"]
    assert authed.get("/api/logs", params={"lines": 0}).status_code == 422
    assert authed.get("/api/logs", params={"lines": 2000}).status_code == 422


def test_logs_unavailable_shape(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.logs as logs_adapter

    def _boom(cmd: list[str]) -> str:
        raise OSError("no journal")

    monkeypatch.setattr(logs_adapter, "_run", _boom)
    response = authed.get("/api/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "journalctl" in body["hint"]


def test_backup_restore_fixture_parity() -> None:
    payload = json.loads((FIXTURES / "backup-restore.json").read_text())
    dto = BackupRestoreDTO.model_validate(payload["request"])
    assert dto.model_dump(mode="json") == payload["request"]
    assert payload["response"] == {"ok": True}


def test_logs_fixture_parity(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.logs as logs_adapter

    payload = json.loads((FIXTURES / "logs.json").read_text())
    assert set(payload) == {"available", "unavailable"}
    assert payload["available"]["available"] is True
    assert isinstance(payload["available"]["lines"], list)
    assert payload["unavailable"]["available"] is False
    assert isinstance(payload["unavailable"]["hint"], str)

    monkeypatch.setattr(
        logs_adapter,
        "_run",
        lambda cmd: "\n".join(
            json.dumps({"MESSAGE": line}) for line in payload["available"]["lines"]
        ),
    )
    assert authed.get("/api/logs").json() == payload["available"]

    def _boom(cmd: list[str]) -> str:
        raise OSError(payload["unavailable"]["hint"])

    monkeypatch.setattr(logs_adapter, "_run", _boom)
    body = authed.get("/api/logs").json()
    assert body["available"] is False
    assert body["hint"].endswith(payload["unavailable"]["hint"])
