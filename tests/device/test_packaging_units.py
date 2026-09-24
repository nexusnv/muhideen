"""Packaging systemd units: PRD §7.2 directives parsed with interpolation off (1A-8).

`configparser(interpolation=None)` is mandatory: systemd's `%H` specifier
in the mDNS `ExecStart` would explode the default `%` interpolation.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


def _unit(name: str) -> configparser.ConfigParser:
    """Parse one unit file the way every device test must: no interpolation."""
    parser = configparser.ConfigParser(interpolation=None)
    parsed = parser.read(PACKAGING / name)
    assert parsed, f"{name} missing from packaging/"
    return parser


def test_unit_files_parse_with_percent_specifiers() -> None:
    service = _unit("muhideen.service")
    mdns = _unit("muhideen-mdns.service")
    # The %H instance name is exactly why interpolation stays off.
    assert "%H" in mdns["Service"]["execstart"]
    assert {"Unit", "Service", "Install"} <= set(service.sections())
    assert {"Unit", "Service", "Install"} <= set(mdns.sections())


def test_service_unit_orders_after_network_and_time_sync() -> None:
    service = _unit("muhideen.service")
    after = service["Unit"]["after"]
    assert "network-online.target" in after
    assert "time-sync.target" in after
    assert "network-online.target" in service["Unit"]["wants"]


def test_service_unit_runtime_directives_and_exec_start() -> None:
    service = _unit("muhideen.service")
    unit_service = service["Service"]
    assert unit_service["restart"] == "always"
    assert unit_service["restartsec"] == "5"
    assert unit_service["user"] == "muhideen"
    assert "PYTHONUNBUFFERED=1" in unit_service["environment"]
    exec_start = unit_service["execstart"]
    assert "bin/muhideen --host" in exec_start
    assert "--host 0.0.0.0" in exec_start
    assert "--port 8000" in exec_start
    assert "--db /var/lib/muhideen/muhideen.db" in exec_start
    assert service["Install"]["wantedby"] == "multi-user.target"


def test_mdns_unit_publishes_and_tracks_backend() -> None:
    mdns = _unit("muhideen-mdns.service")
    exec_start = mdns["Service"]["execstart"]
    assert "avahi-publish-service" in exec_start
    assert "_muhideen._tcp" in exec_start
    assert "8000" in exec_start
    assert mdns["Service"]["partof"] == "muhideen.service"
    assert "avahi-daemon.service" in mdns["Unit"]["after"]
    assert mdns["Service"]["restart"] == "always"
