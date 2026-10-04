"""Console entrypoint: parse flags, build the file-backed app, run uvicorn.

Argparse only — no environment-variable magic (1A-7 decision 2 precedent).
Defaults serve `./config/muhideen.json` on loopback; the systemd unit passes
`--host 0.0.0.0 --port 8000 --config /etc/muhideen/muhideen.json` so the
device answers on the LAN and the FR-6.3 `.local` name resolves.
A missing config file fails fast (non-zero exit); there is no seed flow.
"""

from __future__ import annotations

import argparse

import uvicorn

from muhideen.api.app import create_production_app


def _parser() -> argparse.ArgumentParser:
    """Build the serve CLI: JSON config path plus bind host and port."""
    parser = argparse.ArgumentParser(
        prog="muhideen", description="Serve the Muhideen backend HTTP API."
    )
    parser.add_argument(
        "--config",
        default="./config/muhideen.json",
        help="main JSON config path",
    )
    parser.add_argument(
        "--prayer-buffer",
        default="./config/prayer_buffer.json",
        help="timetable cache path",
    )
    parser.add_argument(
        "--media-dir",
        default="./media",
        help="media directory (adhan audio plus playlist images)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (0.0.0.0 exposes the API to the LAN)",
    )
    parser.add_argument("--port", default=8000, type=int, help="TCP port to bind")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the backend with a single worker (decision 3)."""
    args = _parser().parse_args(argv)
    app = create_production_app(
        args.config,
        prayer_buffer=args.prayer_buffer,
        media_dir=args.media_dir,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
