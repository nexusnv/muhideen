"""Admin token auth + shared write lock + error shape + media mount.

Cross-cutting plumbing for the token-gated admin surface (spec §1). The
business surface (config PATCH, playlists, displays, manual-days, media,
backup/logs) is out of scope here, so the gated endpoints exercised here
are synthetic probes reusing the real `require_admin` / `AdminRoute` /
`get_write_lock` / `invalid` foundation. `tests/test_no_admin.py` must
keep passing: the real admin router stays empty here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRouter
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import BaseModel

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"

KL = timezone(timedelta(hours=8))
PINNED_START = datetime(2025, 10, 20, 12, 20, tzinfo=KL)
ADMIN_TOKEN = "probe-admin-token-0123456789abcdef"


class FakeClock:
    """Pinned clock; mirrors tests/e2e/conftest.py (no shared import)."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono


class _ValidateBody(BaseModel):
    name: str


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _copy_config(tmp_path: Path) -> Path:
    """Copy the golden example to tmp (never mutates the repo)."""
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    raw = json.loads(dest.read_text())
    raw["schedule"]["lat"] = 3.07
    raw["schedule"]["lon"] = 101.69
    dest.write_text(json.dumps(raw, indent=2) + "\n")
    return dest


def _probe_app(
    tmp_path: Path, *, admin_token: str | None, media_dir: Path | None = None
) -> Any:
    """File-backed app plus synthetic gated probes on the real foundation."""
    from muhideen.adapters.file_config import (
        FilePlaylistRepo,
        FilePrayerRepo,
        FileSettingsRepo,
        load_config_file,
    )
    from muhideen.adapters.sse_bus import SSEBus
    from muhideen.api.admin import (
        AdminRoute,
        get_write_lock,
        invalid,
        require_admin,
    )
    from muhideen.api.app import AppDeps, create_app

    dest = _copy_config(tmp_path)
    cfg = load_config_file(dest)
    media = media_dir if media_dir is not None else tmp_path / "media"
    if media_dir is None:
        media.mkdir(exist_ok=True)
    deps = AppDeps(
        settings_repo=FileSettingsRepo(dest),
        prayer_repo=FilePrayerRepo(tmp_path / "buffer.json", cfg.schedule.manual_days),
        clock=FakeClock(PINNED_START),  # type: ignore[arg-type]
        event_bus=SSEBus(),
        playlist_repo=FilePlaylistRepo(dest),
        media_dir=media,
        config_path=dest,
        admin_token=admin_token,
    )
    assert isinstance(deps.write_lock, threading.Lock)
    app = create_app(deps)
    assert app.state.admin_token == admin_token
    assert app.state.write_lock is deps.write_lock

    probes = APIRouter(route_class=AdminRoute, dependencies=[Depends(require_admin)])

    @probes.post("/api/probe/write")
    def _write(request: Request) -> dict[str, object]:
        """Write-category probe: holds the shared lock around its update."""
        with get_write_lock(request):
            held = request.app.state.write_lock.locked()
        return {"ok": True, "held_lock": held}

    @probes.post("/api/probe/validate")
    def _validate(body: _ValidateBody) -> dict[str, bool]:
        """Validate-category probe: mapped ValueError → 422 body-loc shape."""
        if not body.name.strip():
            raise invalid("name", "name must not be blank")
        return {"ok": True}

    @probes.get("/api/probe/logs")
    def _logs() -> dict[str, list[str]]:
        """Logs-category probe (gated: leaks journald paths/errors)."""
        return {"lines": ["booted"]}

    @probes.get("/api/probe/export")
    def _export() -> dict[str, bool]:
        """Export-category probe (gated: bundles buffer+media)."""
        return {"ok": True}

    @probes.get("/api/probe/boom")
    def _boom() -> None:
        """Crash-category probe: unexpected 500s are still audited."""
        raise RuntimeError("boom")

    app.include_router(probes)
    return app


@pytest.fixture
def disabled_client(tmp_path: Path) -> Any:
    """App booted with no token file: every gated probe is disabled."""
    with TestClient(_probe_app(tmp_path, admin_token=None)) as client:
        yield client


@pytest.fixture
def enabled_client(tmp_path: Path) -> Any:
    """App booted with a token: probes enforce Bearer comparison."""
    with TestClient(_probe_app(tmp_path, admin_token=ADMIN_TOKEN)) as client:
        yield client


GATED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("post", "/api/probe/write", {"probe": 1}),
    ("post", "/api/probe/validate", {"name": "ok"}),
    ("get", "/api/probe/logs", None),
    ("get", "/api/probe/export", None),
]


@pytest.mark.parametrize("token", [None, "any-token"])
@pytest.mark.parametrize(("method", "path", "payload"), GATED)
def test_disabled_gated_probes_are_503_even_with_bearer(
    disabled_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    token: str | None,
) -> None:
    """Disabled-check first: any token while disabled still gets 503."""
    headers = _auth(token) if token is not None else {}
    response = disabled_client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "admin writes disabled"}


def test_disabled_public_reads_stay_200(disabled_client: TestClient) -> None:
    """Public reads keep serving while admin writes are disabled."""
    assert disabled_client.get("/api/version").status_code == 200
    day = disabled_client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert day.status_code == 200
    event = disabled_client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert event.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abcdef"},
    ],
)
@pytest.mark.parametrize(("method", "path", "payload"), GATED)
def test_enabled_bad_credentials_are_401_with_challenge_never_403(
    enabled_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
) -> None:
    """Missing/wrong/empty Bearer → 401 + challenge (HTTPBearer never 403)."""
    response = enabled_client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid admin token"}
    assert response.headers.get("www-authenticate") == "Bearer"


def test_enabled_valid_token_serves_write_holding_shared_lock(
    enabled_client: TestClient,
) -> None:
    """A valid Bearer serves the write while holding the shared lock."""
    response = enabled_client.post(
        "/api/probe/write", json={"probe": 1}, headers=_auth(ADMIN_TOKEN)
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "held_lock": True}


def test_enabled_valid_token_serves_validate_logs_export(
    enabled_client: TestClient,
) -> None:
    """A valid Bearer serves every other gated category."""
    headers = _auth(ADMIN_TOKEN)
    assert enabled_client.post(
        "/api/probe/validate", json={"name": "Masjid"}, headers=headers
    ).json() == {"ok": True}
    assert enabled_client.get("/api/probe/logs", headers=headers).status_code == 200
    assert enabled_client.get("/api/probe/export", headers=headers).status_code == 200


def test_gated_requests_audit_peer_method_path_status_without_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every gated request logs peer/method/path/status — never the token."""
    with (
        TestClient(_probe_app(tmp_path, admin_token=ADMIN_TOKEN)) as client,
        caplog.at_level(logging.INFO, logger="muhideen.api.admin"),
    ):
        assert (
            client.post(
                "/api/probe/write", json={}, headers=_auth(ADMIN_TOKEN)
            ).status_code
            == 200
        )
        assert client.get("/api/probe/logs").status_code == 401
        assert (
            client.post(
                "/api/probe/validate", json={}, headers=_auth(ADMIN_TOKEN)
            ).status_code
            == 422
        )
    messages = [record.getMessage() for record in caplog.records]
    assert "testclient POST /api/probe/write -> 200" in messages
    assert "testclient GET /api/probe/logs -> 401" in messages
    assert "testclient POST /api/probe/validate -> 422" in messages
    assert all(ADMIN_TOKEN not in message for message in messages)


def test_read_admin_token_missing_empty_blank_unreadable_is_none(
    tmp_path: Path,
) -> None:
    """Token file problems never yield a comparable token."""
    from muhideen.api.admin import read_admin_token

    assert read_admin_token(None) is None
    assert read_admin_token(tmp_path / "absent") is None
    empty = tmp_path / "empty"
    empty.write_text("")
    assert read_admin_token(empty) is None
    blank = tmp_path / "blank"
    blank.write_text("  \n\t\n")
    assert read_admin_token(blank) is None
    assert read_admin_token(tmp_path) is None


def test_read_admin_token_returns_stripped_line(tmp_path: Path) -> None:
    """A one-line token file loads without its trailing newline."""
    from muhideen.api.admin import read_admin_token

    token_file = tmp_path / "admin_token"
    token_file.write_text("s3cret-token\n")
    assert read_admin_token(token_file) == "s3cret-token"


def test_read_admin_token_binary_bytes_is_none_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Undecodable bytes disable admin writes, never crash the read."""
    from muhideen.api.admin import read_admin_token

    binary = tmp_path / "admin_token"
    binary.write_bytes(b"\xff\xfe\x00\x01-not-utf8")
    with caplog.at_level(logging.WARNING, logger="muhideen.api.admin"):
        assert read_admin_token(binary) is None
    assert any(
        "admin writes disabled" in record.getMessage() for record in caplog.records
    )


def test_binary_token_file_boots_with_admin_disabled(tmp_path: Path) -> None:
    """A binary token file boots disabled: gated 503s, public reads serve."""
    from muhideen.api.admin import AdminRoute, require_admin
    from muhideen.api.app import create_production_app

    dest = _copy_config(tmp_path)
    (tmp_path / "admin_token").write_bytes(b"\xff\xfe\x00\x01-not-utf8")
    app = create_production_app(dest, run_background=False)
    assert app.state.admin_token is None

    probes = APIRouter(route_class=AdminRoute, dependencies=[Depends(require_admin)])

    @probes.get("/api/probe/ping")
    def _ping() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(probes)
    with TestClient(app) as client:
        assert client.get("/api/probe/ping").status_code == 503
        assert client.get("/api/version").status_code == 200
        day = client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
        )
        assert day.status_code == 200


def test_production_default_token_file_is_config_sibling_and_read_once(
    tmp_path: Path,
) -> None:
    """Default is always <config-dir>/admin_token; boot reads it once."""
    from muhideen.api.admin import admin_token_file_for_config
    from muhideen.api.app import create_production_app

    dest = _copy_config(tmp_path)
    assert admin_token_file_for_config(dest) == tmp_path / "admin_token"
    assert admin_token_file_for_config("/etc/muhideen/muhideen.json") == Path(
        "/etc/muhideen/admin_token"
    )
    assert create_production_app(dest, run_background=False).state.admin_token is None
    (tmp_path / "admin_token").write_text(f"{ADMIN_TOKEN}\n")
    booted = create_production_app(dest, run_background=False)
    assert booted.state.admin_token == ADMIN_TOKEN
    with TestClient(booted) as client:
        assert client.get("/api/version").status_code == 200
    # Post-boot file changes have no effect until restart (read-once).
    (tmp_path / "admin_token").write_text("rotated-token\n")
    assert booted.state.admin_token == ADMIN_TOKEN
    # A blank file at boot disables admin writes.
    (tmp_path / "admin_token").write_text("   \n")
    assert create_production_app(dest, run_background=False).state.admin_token is None
    alt = tmp_path / "alt_token"
    alt.write_text("alt-secret\n")
    assert (
        create_production_app(
            dest, run_background=False, admin_token_file=alt
        ).state.admin_token
        == "alt-secret"
    )


def test_service_resolves_admin_token_file_next_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() derives the token default from --config; explicit flag wins."""
    from muhideen import service

    seen: dict[str, object] = {}

    def _app(config: str, **kwargs: object) -> object:
        seen["config"] = config
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(service, "create_production_app", _app)
    monkeypatch.setattr(service, "uvicorn", SimpleNamespace(run=lambda *a, **k: None))
    cfg = str(tmp_path / "muhideen.json")
    service.main(["--config", cfg])
    assert seen["kwargs"] == {
        "prayer_buffer": "./config/prayer_buffer.json",
        "media_dir": "./media",
        "admin_token_file": str(tmp_path / "admin_token"),
    }
    alt = str(tmp_path / "custom_token")
    service.main(["--config", cfg, "--admin-token-file", alt])
    assert seen["kwargs"] == {
        "prayer_buffer": "./config/prayer_buffer.json",
        "media_dir": "./media",
        "admin_token_file": alt,
    }


def test_parser_admin_token_file_defaults_to_derive_from_config() -> None:
    """argparse leaves the default empty so main() can derive it from --config."""
    from muhideen.service import _parser

    assert _parser().parse_args([]).admin_token_file is None


def test_media_mount_is_unconditional_after_mkdir(tmp_path: Path) -> None:
    """A missing media dir is created at boot and served without a restart."""
    fresh = tmp_path / "fresh-media"
    assert not fresh.exists()
    with TestClient(_probe_app(tmp_path, admin_token=None, media_dir=fresh)) as client:
        assert fresh.is_dir()
        (fresh / "hello.txt").write_text("salam")
        response = client.get("/media/hello.txt")
    assert response.status_code == 200
    assert response.text == "salam"


def test_media_path_blocked_by_file_warns_and_keeps_serving(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A media path blocked by a file warns; boot keeps serving, no /media."""
    blocker = tmp_path / "media-blocker"
    blocker.write_text("not a dir")
    with (
        caplog.at_level(logging.WARNING, logger="muhideen.api.app"),
        TestClient(_probe_app(tmp_path, admin_token=None, media_dir=blocker)) as client,
    ):
        assert client.get("/api/version").status_code == 200
        assert client.get("/media/hello.txt").status_code == 404
    assert any("/media not mounted" in record.getMessage() for record in caplog.records)


def test_mapped_value_errors_use_body_loc_convention(
    enabled_client: TestClient,
) -> None:
    """Non-Pydantic ValueErrors map to 422 with loc ["body", <field>]."""
    response = enabled_client.post(
        "/api/probe/validate", json={"name": "   "}, headers=_auth(ADMIN_TOKEN)
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "loc": ["body", "name"],
                "msg": "name must not be blank",
                "type": "value_error",
            }
        ]
    }


def test_invalid_helper_supports_nested_fields() -> None:
    """Nested merges locate the field: ["body", "aladhan", "base_url"]."""
    from muhideen.api.admin import invalid

    exc = invalid(("aladhan", "base_url"), "must use http(s)")
    assert exc.status_code == 422
    assert exc.detail == [
        {
            "loc": ["body", "aladhan", "base_url"],
            "msg": "must use http(s)",
            "type": "value_error",
        }
    ]


def test_admin_router_registered_without_business_routes_yet() -> None:
    """The gated router is mounted but exposes no business routes yet."""
    from muhideen.api.admin import admin_router

    assert admin_router.routes == []


def test_enabled_non_ascii_bearer_is_401_with_challenge_never_500() -> None:
    """Non-ASCII Bearer is a 401 mismatch, never a 500.

    ``secrets.compare_digest`` on ``str`` raises ``TypeError`` for non-ASCII
    input; the gate compares UTF-8 bytes so every non-ASCII token — direct
    or raw-UTF-8-bytes decoded as latin-1 per ASGI — is a 401 mismatch.
    """
    from muhideen.api.admin import require_admin

    raw_utf8_as_latin1 = "tökén".encode().decode("latin-1")
    for token in ("tökén-üñïcödé-☃", raw_utf8_as_latin1):
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/api/probe/write",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(admin_token=ADMIN_TOKEN)),
        }
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_admin(Request(scope), credentials))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "invalid admin token"
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_unexpected_500_inside_gated_route_is_still_audited(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A RuntimeError behind the gate still emits an audit line (-> 500)."""
    with (
        TestClient(
            _probe_app(tmp_path, admin_token=ADMIN_TOKEN),
            raise_server_exceptions=False,
        ) as client,
        caplog.at_level(logging.INFO, logger="muhideen.api.admin"),
    ):
        response = client.get("/api/probe/boom", headers=_auth(ADMIN_TOKEN))
        assert response.status_code == 500
    messages = [record.getMessage() for record in caplog.records]
    assert "testclient GET /api/probe/boom -> 500" in messages
    assert all(ADMIN_TOKEN not in message for message in messages)


def test_missing_app_state_fails_safe_to_503_never_500() -> None:
    """App.state without admin_token/write_lock denies writes (503)."""
    from muhideen.api.admin import get_write_lock, require_admin

    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/probe/write",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    request = Request(scope)
    with pytest.raises(HTTPException) as auth_exc:
        asyncio.run(require_admin(request, None))
    assert auth_exc.value.status_code == 503
    assert auth_exc.value.detail == "admin writes disabled"
    with pytest.raises(HTTPException) as lock_exc:
        get_write_lock(request)
    assert lock_exc.value.status_code == 503
    assert lock_exc.value.detail == "admin writes disabled"
