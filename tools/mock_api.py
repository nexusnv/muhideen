"""Stdlib-only mock API for frontend-only development.

Serves api/fixtures/ on :8001 with the same paths as docs/api-contract.md.
No third-party deps. Run: uv run tools/mock_api.py [--port 8001]

Gated endpoints (every write, plus GET /api/backup/export and
GET /api/logs) require ``Authorization: Bearer dev-admin-token`` — the
fixed documented token. A missing/wrong Bearer answers 401 with the
same shape as the device (``WWW-Authenticate: Bearer`` included);
``MOCK_ADMIN_DISABLED=1`` answers 503 ``admin writes disabled`` for
every gated endpoint, also the same shape as the device.

State is in-memory only (created playlists/displays/pins overlay the
fixtures until restart). Validation parity is intentionally canned:
JSON writes answer 422 only when the request body byte-matches the
family's ``admin-*-422.json`` fixture (or ``?canned=422`` is set);
every other body succeeds with the ``admin-*-200/201.json`` fixture.
Query validation (``/api/logs?lines=``) is real range checking.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "api" / "fixtures"
EXAMPLE_CONFIG = ROOT / "config" / "muhideen.example.json"

DEV_TOKEN = "dev-admin-token"
"""Fixed mock Bearer token (documented here; device tokens differ)."""

DISABLED_DETAIL = "admin writes disabled"
INVALID_DETAIL = "invalid admin token"

_STATE: dict[str, Any] = {"playlists": {}, "displays": {}, "pins": {}}
"""In-memory overlays (no disk): created rows served until restart."""


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _disabled() -> bool:
    return os.environ.get("MOCK_ADMIN_DISABLED", "0") == "1"


_BAD_BODY = {
    ("PATCH", "/api/config/masjid"): "admin-masjid-422.json",
    ("PATCH", "/api/config/schedule"): "admin-schedule-422.json",
    ("POST", "/api/playlists"): "admin-playlist-422.json",
    ("PUT-DISPLAY", ""): "admin-display-422.json",
    ("PUT-PIN", ""): "admin-pin-422.json",
}
"""Request samples that trigger the canned 422 per family (byte match)."""

_CANNED_422: dict[str, bytes] = {
    "masjid": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "name"],
                    "msg": "name must not be blank",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
    "schedule": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "sync_provider"],
                    "msg": "aladhan needs coordinates",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
    "playlist": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "title"],
                    "msg": "title must not be blank",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
    "display": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "name"],
                    "msg": "name must not be blank",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
    "pin": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "date"],
                    "msg": "date is required",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
    "media": json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "kind"],
                    "msg": "kind 'image' needs one of jpg/jpeg/png/webp",
                    "type": "value_error",
                }
            ]
        }
    ).encode(),
}


def _sample_zip() -> bytes:
    """Fixed backup-export sample: config + manifest members only."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("muhideen.json", EXAMPLE_CONFIG.read_bytes())
        zf.writestr("manifest.json", _read("admin-backup-manifest.json"))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send_json(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _gate(self) -> bool:
        """401/503 gate for writes + logs + export (device-identical shapes)."""
        if _disabled():
            self._send_json(
                json.dumps({"detail": DISABLED_DETAIL}).encode(), status=503
            )
            return False
        if self.headers.get("Authorization") != f"Bearer {DEV_TOKEN}":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("WWW-Authenticate", "Bearer")
            body = json.dumps({"detail": INVALID_DETAIL}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return False
        return True

    def _body(self) -> bytes:
        if not hasattr(self, "_cached_body"):
            length = int(self.headers.get("Content-Length") or 0)
            self._cached_body = self.rfile.read(length) if length else b""
        return self._cached_body

    def _wants_422(self, family: str, bad_fixture: str | None) -> bool:
        query = parse_qs(urlparse(self.path).query)
        if query.get("canned") == ["422"]:
            return True
        if bad_fixture is None:
            return False
        try:
            want = _read(bad_fixture)
        except OSError:
            return False
        got = self._body()
        if got == want:
            return True
        try:
            return json.loads(got.decode()) == json.loads(want.decode())
        except ValueError:
            return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/prayer-day":
            self._send_json(_read("prayer-day.json"))
        elif path == "/api/next-event":
            self._send_json(_read("next-event.json"))
        elif path == "/api/events":
            body = _read("events-stream.txt")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/version":
            self._send_json(_read("version.json"))
        elif path == "/api/config":
            self._send_json(EXAMPLE_CONFIG.read_bytes())
        elif path == "/api/config/masjid":
            self._send_json(_read("admin-masjid-200.json"))
        elif path == "/api/config/schedule":
            self._send_json(_read("admin-schedule-200.json"))
        elif path == "/api/playlists":
            base = [json.loads(_read("admin-playlist-201.json"))]
            self._send_json(json.dumps([*_STATE["playlists"].values(), *base]).encode())
        elif path == "/api/playlists/preview":
            self._send_json(
                json.dumps(
                    {"stage": "clock", "active_playlists": ["taraweeh"]}
                ).encode()
            )
        elif path == "/api/displays":
            base = {"main-hall": json.loads(_read("admin-display-201.json"))}
            self._send_json(json.dumps({**base, **_STATE["displays"]}).encode())
        elif path == "/api/config/manual-days":
            pins = [json.loads(_read("admin-pin-200.json"))]
            self._send_json(
                json.dumps(
                    {"source": "inline", "pins": [*_STATE["pins"].values(), *pins]}
                ).encode()
            )
        elif path == "/api/media":
            self._send_json(
                json.dumps(
                    [{"path": "playlists/slide.png", "size_bytes": 204800}]
                ).encode()
            )
        elif path == "/api/backup/export":
            if not self._gate():
                return
            body = _sample_zip()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="muhideen-backup-20251020-122000.zip"',
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/logs":
            if not self._gate():
                return
            raw = (query.get("lines") or ["200"])[0]
            try:
                lines = int(raw)
            except ValueError:
                lines = 0
            if not 1 <= lines <= 1000:
                self._send_json(
                    json.dumps(
                        {
                            "detail": [
                                {
                                    "loc": ["query", "lines"],
                                    "msg": (
                                        "Input should be an integer between 1 and 1000"
                                    ),
                                    "type": "value_error",
                                }
                            ]
                        }
                    ).encode(),
                    status=422,
                )
                return
            self._send_text(_read("admin-logs.txt"), "text/plain")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/playlists":
            if not self._gate():
                return
            body = self._body()
            if self._wants_422("playlist", _BAD_BODY[("POST", "/api/playlists")]):
                self._send_json(_CANNED_422["playlist"], status=422)
                return
            try:
                payload = json.loads(body.decode())
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and payload.get("id") == "taraweeh":
                self._send_json(
                    json.dumps(
                        {"detail": "duplicate playlist id: 'taraweeh'"}
                    ).encode(),
                    status=409,
                )
                return
            if isinstance(payload, dict) and payload.get("id"):
                _STATE["playlists"][payload["id"]] = payload
            self._send_json(_read("admin-playlist-201.json"), status=201)
        elif path == "/api/playlists/taraweeh/items":
            if not self._gate():
                return
            self._send_json(
                json.dumps(
                    {
                        "image_path": "playlists/slide.png",
                        "duration_s": 10,
                        "sort_order": 0,
                    }
                ).encode(),
                status=201,
            )
        elif path == "/api/media":
            if not self._gate():
                return
            raw = self._body().decode("latin-1")
            filename = re.search(r'filename="([^"]*)"', raw)
            kind = re.search(r'name="kind"\r\n\r\n([^\r]*)', raw)
            name = filename.group(1) if filename else ""
            kind_value = kind.group(1).strip() if kind else ""
            mismatch = kind_value == "image" and name.lower().endswith(".mp3")
            query = parse_qs(urlparse(self.path).query)
            if mismatch or query.get("canned") == ["422"]:
                self._send_json(_CANNED_422["media"], status=422)
                return
            self._send_json(_read("admin-media-201.json"), status=201)
        elif path == "/api/backup/restore":
            if not self._gate():
                return
            query = parse_qs(urlparse(self.path).query)
            if query.get("canned") == ["422"]:
                self._send_json(_read("admin-backup-422.json"), status=422)
                return
            self._send_json(_read("admin-restore-ok.json"))
        elif path in (
            "/api/config/validate",
            "/api/config/masjid/validate",
            "/api/config/schedule/validate",
            "/api/config/timing/validate",
            "/api/config/theme/validate",
            "/api/config/adhan-audio/validate",
            "/api/config/manual-days/validate",
        ):
            if not self._gate():
                return
            self._send_json(json.dumps({"ok": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not self._gate():
            return
        if path == "/api/config/masjid":
            if self._wants_422("masjid", _BAD_BODY[("PATCH", "/api/config/masjid")]):
                self._send_json(_CANNED_422["masjid"], status=422)
                return
            self._send_json(
                json.dumps({"ok": True, "restart_required": False}).encode()
            )
        elif path == "/api/config/schedule":
            if self._wants_422(
                "schedule", _BAD_BODY[("PATCH", "/api/config/schedule")]
            ):
                self._send_json(_CANNED_422["schedule"], status=422)
                return
            self._send_json(
                json.dumps({"ok": True, "restart_required": False}).encode()
            )
        elif path in (
            "/api/config/timing",
            "/api/config/theme",
            "/api/config/adhan-audio",
        ):
            self._send_json(
                json.dumps({"ok": True, "restart_required": False}).encode()
            )
        elif path.startswith("/api/playlists/"):
            self._send_json(_read("admin-playlist-201.json"))
        elif path.startswith("/api/displays/"):
            display_id = path.rsplit("/", 1)[-1]
            if display_id == "missing":
                self._send_json(
                    json.dumps({"detail": "unknown display id: 'missing'"}).encode(),
                    status=404,
                )
                return
            self._send_json(_read("admin-display-201.json"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if not self._gate():
            return
        if path.startswith("/api/displays/"):
            if self._wants_422("display", _BAD_BODY[("PUT-DISPLAY", "")]):
                self._send_json(_CANNED_422["display"], status=422)
                return
            display_id = path.rsplit("/", 1)[-1]
            _STATE["displays"][display_id] = json.loads(_read("admin-display-201.json"))
            self._send_json(_read("admin-display-201.json"), status=201)
        elif path.startswith("/api/config/manual-days/"):
            if self._wants_422("pin", _BAD_BODY[("PUT-PIN", "")]):
                self._send_json(_CANNED_422["pin"], status=422)
                return
            self._send_json(_read("admin-pin-200.json"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self._gate():
            return
        if (
            path.startswith("/api/playlists/")
            or path.startswith("/api/displays/")
            or path.startswith("/api/media/")
            or path.startswith("/api/config/manual-days/")
        ):
            if path.rsplit("/", 1)[-1] in ("missing", "nope.mp3"):
                self._send_json(
                    json.dumps({"detail": "not found"}).encode(), status=404
                )
                return
            self._send_json(json.dumps({"ok": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock-api serving {FIXTURES} on http://localhost:{args.port}")
    print("gated endpoints require: Authorization: Bearer dev-admin-token")
    server.serve_forever()


if __name__ == "__main__":
    main()
