"""Console entrypoint: parse flags, build the production app, run uvicorn.

Argparse only — no environment-variable magic (1A-7 decision 2 precedent).
Defaults serve `./muhideen.db` on loopback; the systemd unit passes
`--host 0.0.0.0 --port 8000 --db /var/lib/muhideen/muhideen.db` so the
device answers on the LAN and the FR-6.3 `.local` name resolves.
"""

from __future__ import annotations

import argparse

import uvicorn

from muhideen.api.app import create_production_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muhideen", description="Serve the Muhideen backend HTTP API."
    )
    parser.add_argument("--db", default="./muhideen.db", help="SQLite database path")
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
    app = create_production_app(args.db)
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
