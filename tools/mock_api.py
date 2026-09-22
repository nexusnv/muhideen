"""Stdlib-only mock API for frontend-only development.

Serves api/fixtures/ on :8001 with the same paths as docs/api-contract.md.
No third-party deps. Run: uv run tools/mock_api.py [--port 8001]
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "api" / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send_json(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/displays/heartbeat":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._send_json(_read("heartbeat-response.json"))
        else:
            self.send_response(404)
            self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock-api serving {FIXTURES} on http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
