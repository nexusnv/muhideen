"""install.sh: syntax, preflight gate, dry-run step order, hostname (1A-8).

Every invocation runs against PATH shims (argv logged to a per-run file,
canned `df` table so preflight can parse it) and the `MUHIDEEN_MEMINFO` /
`MUHIDEEN_SYSTEMD_DIR` seams; only preflight's reads ever execute.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "install.sh"

SHIM_NAMES = (
    "df",
    "apt-get",
    "uv",
    "useradd",
    "chown",
    "systemctl",
    "timedatectl",
    "hostnamectl",
    "curl",
    "muhideen-seed",
)
_LOG_LINE = 'printf \'%s %s\\n\' "${0##*/}" "$*" >> "$SHIM_LOG"\n'
_DF_TABLE = (
    "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on"
    "\\n/dev/root 8000000 2000000 6000000 25%% /\\n'\n"
)


def _run_install(
    tmp_path: Path,
    args: list[str],
    *,
    mem_kb: int,
    tag: str,
    state_db: bool = False,
    seed_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh with shims + seams; a fresh shim set per call."""
    shim_dir = tmp_path / f"bin-{tag}"
    shim_dir.mkdir()
    shim_log = tmp_path / f"shim-{tag}.log"
    for name in SHIM_NAMES:
        body = _LOG_LINE
        if name == "df":
            body += _DF_TABLE
        elif name == "muhideen-seed":
            body += 'exit "${SHIM_SEED_EXIT:-0}"\n'
        shim = shim_dir / name
        shim.write_text("#!/bin/sh\n" + body)
        shim.chmod(0o755)
    meminfo = tmp_path / f"meminfo-{tag}"
    meminfo.write_text(f"MemTotal:       {mem_kb} kB\nMemFree:         1234 kB\n")
    state_dir = tmp_path / f"state-{tag}"
    state_dir.mkdir()
    if state_db:
        (state_dir / "muhideen.db").write_bytes(b"")  # existing installation
    wheels_dir = tmp_path / f"wheels-{tag}"
    wheels_dir.mkdir()  # vendored wheels present (release checkouts ship them)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "MUHIDEEN_MEMINFO": str(meminfo),
        "MUHIDEEN_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "MUHIDEEN_STATE_DIR": str(state_dir),
        "MUHIDEEN_SEED": str(shim_dir / "muhideen-seed"),
        "MUHIDEEN_VENDOR_DIR": str(wheels_dir),
        "SHIM_SEED_EXIT": str(seed_exit),
        "SHIM_LOG": str(shim_log),
    }
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def _shim_log(tmp_path: Path, tag: str) -> str:
    log = tmp_path / f"shim-{tag}.log"
    return log.read_text() if log.exists() else ""


def test_dry_run_logs_ordered_steps_and_sources_lib(tmp_path: Path) -> None:
    # Syntax first — folded into this fn to keep the device count pinned at 8.
    syntax = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True)
    assert syntax.returncode == 0, syntax.stderr.decode()
    source_lines = [
        line
        for line in INSTALL.read_text().splitlines()
        if "lib.sh" in line and line.lstrip().startswith(("source", "."))
    ]
    assert source_lines, "install.sh must source packaging/lib.sh"

    result = _run_install(
        tmp_path, ["--dry-run", "--zone", "SGR01"], mem_kb=2_000_000, tag="dry"
    )
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    unit_dir = tmp_path / "systemd"
    markers = [
        "preflight",
        "apt-get install -y avahi-daemon avahi-utils git curl",
        "uv sync --locked --offline --no-dev --find-links vendor/wheels",
        f"muhideen.service -> {unit_dir}",
        f"muhideen-mdns.service -> {unit_dir}",
        "muhideen-seed --zone SGR01",
        "timedatectl set-ntp true",
        "systemctl enable --now muhideen muhideen-mdns",
        "api/version",
    ]
    indices: list[int] = []
    for marker in markers:
        hits = [i for i, line in enumerate(lines) if marker in line]
        assert hits, f"missing step marker {marker!r} in:\n{result.stdout}"
        indices.append(hits[0])
    assert indices == sorted(indices), list(zip(markers, indices, strict=True))

    # Preflight's reads really execute (df fired); nothing else did.
    shim_log = _shim_log(tmp_path, "dry")
    assert "df" in shim_log
    assert "curl" not in shim_log


def test_preflight_refuses_low_ram_with_force_hint(tmp_path: Path) -> None:
    result = _run_install(tmp_path, ["--zone", "SGR01"], mem_kb=500_000, tag="refuse")
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "--force" in combined
    assert "apt-get" not in combined  # refused before any step runs


def test_force_overrides_preflight_and_proceeds(tmp_path: Path) -> None:
    # --dry-run keeps the continuation proof hermetic: the gate itself runs
    # for real (small MemTotal, --force), later steps print instead of exec.
    result = _run_install(
        tmp_path,
        ["--force", "--dry-run", "--zone", "SGR01"],
        mem_kb=500_000,
        tag="force",
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "re-run with --force" not in combined  # no refusal was raised
    assert "apt-get install -y avahi-daemon avahi-utils git curl" in combined


def test_hostname_flag_controls_hostnamectl_step(tmp_path: Path) -> None:
    default = _run_install(
        tmp_path, ["--dry-run", "--zone", "SGR01"], mem_kb=2_000_000, tag="host-default"
    )
    assert default.returncode == 0, default.stderr
    assert "hostnamectl set-hostname muhideen" in default.stdout

    skipped = _run_install(
        tmp_path,
        ["--dry-run", "--zone", "SGR01", "--hostname", ""],
        mem_kb=2_000_000,
        tag="host-empty",
    )
    assert skipped.returncode == 0, skipped.stderr
    assert "hostnamectl" not in skipped.stdout


def test_first_boot_without_zone_refuses_before_any_step(tmp_path: Path) -> None:
    # First boot (no state db) without --zone: die before apt/useradd/units,
    # so a mistyped invocation never leaves a half-installed device.
    refused = _run_install(tmp_path, [], mem_kb=2_000_000, tag="nozone")
    assert refused.returncode == 1
    combined = refused.stdout + refused.stderr
    assert "--zone" in combined
    log = _shim_log(tmp_path, "nozone")
    assert "apt-get" not in log and "useradd" not in log  # nothing mutated

    # An existing installation needs no --zone: every step proceeds.
    installed = _run_install(
        tmp_path, ["--dry-run"], mem_kb=2_000_000, tag="reinstall", state_db=True
    )
    assert installed.returncode == 0, installed.stderr
    assert "apt-get install -y avahi-daemon avahi-utils git curl" in installed.stdout


def test_seed_exit_3_tolerated_but_other_codes_die(tmp_path: Path) -> None:
    # Seed's own year-fetch failure (exit 3, configured-but-unsynced) must not
    # abort the install: services still enable and the scheduler retries.
    tolerated = _run_install(
        tmp_path, ["--zone", "SGR01"], mem_kb=2_000_000, tag="seed3", seed_exit=3
    )
    assert tolerated.returncode == 0, tolerated.stderr
    assert "scheduler will retry" in tolerated.stdout
    # The state dir is created before seeding: useradd's --create-home is
    # skipped when muhideen already exists, so the dir can't ride on it.
    assert f"mkdir -p {tmp_path / 'state-seed3'}" in tolerated.stdout
    assert "systemctl enable --now" in _shim_log(tmp_path, "seed3")

    # Any other seed failure still aborts loudly.
    failed = _run_install(
        tmp_path, ["--zone", "SGR01"], mem_kb=2_000_000, tag="seed1", seed_exit=1
    )
    assert failed.returncode == 1
