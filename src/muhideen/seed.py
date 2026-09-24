"""Console entrypoint `muhideen-seed`: configure the zone, sync the year.

First boot (no settings row): create `Settings` from `--zone` — required
in that branch — then sync. Configured installations always sync the
**configured** zone (one zone per installation): a differing `--zone` or a
`--masjid-name` is a logged no-op, never an overwrite. The sync itself is
`run_sync` verbatim from 1A-6, whose `ConfigError`-propagates contract is
what makes the branch split above possible.
"""

from __future__ import annotations

import argparse
import sys
from zoneinfo import ZoneInfo

from muhideen.adapters.jakim_esolat import HttpJAKIMClient
from muhideen.adapters.migrate import migrate
from muhideen.adapters.scheduler import run_sync
from muhideen.adapters.sqlite_repo import (
    Database,
    SqlitePrayerRepo,
    SqliteSettingsRepo,
)
from muhideen.adapters.system_clock import SystemClock
from muhideen.core.errors import ConfigError
from muhideen.core.values import Settings

_PROD_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muhideen-seed",
        description="Seed a Muhideen database: configure the zone, sync the year.",
    )
    parser.add_argument("--db", default="./muhideen.db", help="SQLite database path")
    parser.add_argument("--zone", help="JAKIM zone (required until configured)")
    parser.add_argument("--masjid-name", help="masjid display name (first boot only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Configure-or-sync, then one full-year sync; 0 on success (decision 3)."""
    args = _parser().parse_args(argv)
    database = Database(args.db)
    migrate(database)
    settings_repo = SqliteSettingsRepo(database)
    prayer_repo = SqlitePrayerRepo(database)
    try:
        settings = settings_repo.load()
    except ConfigError:
        if args.zone is None:
            print(
                "error: --zone is required on an unconfigured installation",
                file=sys.stderr,
            )
            return 2
        settings = Settings(
            masjid_name=args.masjid_name or "",
            zone=args.zone,
            hijri_offset=0,
        )
        settings_repo.save(settings)
    else:
        if args.zone is not None and args.zone != settings.zone:
            print(
                f"notice: ignoring --zone {args.zone}; "
                f"this installation is configured for {settings.zone}",
                file=sys.stderr,
            )
    clock = SystemClock(_PROD_TZ)
    count = run_sync(
        client=HttpJAKIMClient(clock=clock),
        prayer_repo=prayer_repo,
        settings_repo=settings_repo,
        clock=clock,
    )
    print(f"synced {count} days for zone {settings.zone}")
    return 0
