"""update.sh + tools/build_vendor.sh: backup order, --check, dirty refusal,
health recovery, vendor cross-download (1A-8).

Every invocation runs against file-local PATH shims (argv logged to a
per-run file; `git status/describe/tag` answered from env seams, curl exit
code from ``SHIM_CURL_EXIT``). update.sh's python backup step is the only
command that executes for real, and only in the health test — where its
``VACUUM INTO`` output is asserted on disk.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

REPO = Path(__file__).resolve().parents[2]
UPDATE = REPO / "update.sh"
BUILD_VENDOR = REPO / "tools" / "build_vendor.sh"

SHIM_NAMES = (
    "git",
    "curl",
    "uv",
    "python",
    "systemctl",
    "sleep",
)
_LOG_LINE = 'printf \'%s %s\\n\' "${0##*/}" "$*" >> "$SHIM_LOG"\n'
_GIT_LOG_LINE = 'printf \'%s %s (pwd=%s)\\n\' "${0##*/}" "$*" "$PWD" >> "$SHIM_LOG"\n'
_GIT_CASE = """case "$1" in
  status)
    if [ -n "${SHIM_GIT_DIRTY:-}" ]; then printf '%s\\n' "$SHIM_GIT_DIRTY"; fi
    ;;
  describe) printf '%s\\n' "${SHIM_GIT_CURRENT:-v1.0.0}" ;;
  tag) printf '%s\\n' "${SHIM_GIT_NEWEST:-v1.0.1}" ;;
esac
"""
_CURL_BODY = """if [ "${SHIM_CURL_EXIT:-0}" -ne 0 ]; then exit "${SHIM_CURL_EXIT}"; fi
printf '{}\\n'
"""


def _shim_env(
    tmp_path: Path,
    tag: str,
    *,
    git_dirty: str = "",
    curl_exit: int = 0,
) -> dict[str, str]:
    """Build a fresh shim set + env for one invocation (file-local, per tag)."""
    shim_dir = tmp_path / f"bin-{tag}"
    shim_dir.mkdir()
    for name in SHIM_NAMES:
        body = _GIT_LOG_LINE if name == "git" else _LOG_LINE
        if name == "git":
            body += _GIT_CASE
        elif name == "curl":
            body += _CURL_BODY
        shim = shim_dir / name
        shim.write_text("#!/bin/sh\n" + body)
        shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "SHIM_LOG": str(tmp_path / f"shim-{tag}.log"),
        "SHIM_CURL_EXIT": str(curl_exit),
        "MUHIDEEN_BACKUP_DIR": str(tmp_path / "backups"),
    }
    if git_dirty:
        env["SHIM_GIT_DIRTY"] = git_dirty
    return env


def _run_update(
    tmp_path: Path,
    args: list[str],
    *,
    tag: str,
    git_dirty: str = "",
    curl_exit: int = 0,
    db: Path | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run update.sh from a throwaway cwd; a fresh shim set per call."""
    env = _shim_env(tmp_path, tag, git_dirty=git_dirty, curl_exit=curl_exit)
    argv = ["bash", str(UPDATE), *args]
    if db is not None:
        argv += ["--db", str(db)]
    workdir = cwd if cwd is not None else tmp_path
    workdir.mkdir(exist_ok=True)
    return subprocess.run(
        argv,
        env=env,
        cwd=workdir,
        capture_output=True,
        text=True,
    )


def _shim_log(tmp_path: Path, tag: str) -> str:
    log = tmp_path / f"shim-{tag}.log"
    return log.read_text() if log.exists() else ""


def test_scripts_parse_and_update_sources_lib() -> None:
    # Syntax first — both new scripts, one fn, keeping the device count at +6.
    for script in (UPDATE, BUILD_VENDOR):
        syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True)
        assert syntax.returncode == 0, f"{script.name}: {syntax.stderr.decode()}"
    source_lines = [
        line
        for line in UPDATE.read_text().splitlines()
        if "lib.sh" in line and line.lstrip().startswith(("source", "."))
    ]
    assert source_lines, "update.sh must source packaging/lib.sh"


def test_dry_run_backs_up_before_checkout(tmp_path: Path) -> None:
    result = _run_update(tmp_path, ["--dry-run"], tag="dry")
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()

    def idx(marker: str) -> int:
        hits = [i for i, line in enumerate(lines) if marker in line]
        assert hits, f"missing step marker {marker!r} in:\n{result.stdout}"
        return hits[0]

    # The backup (`VACUUM INTO` via backup_to) must be traced strictly
    # before the fetch and the tag checkout (PRD §7.2 OTA, decision 5).
    assert idx("backup_to") < idx("git fetch --tags") < idx("git checkout")

    # Every git command runs against this checkout, not the caller's cwd.
    assert f"pwd={REPO}" in _shim_log(tmp_path, "dry")


def test_check_reports_versions_without_mutating(tmp_path: Path) -> None:
    result = _run_update(tmp_path, ["--check"], tag="check")
    assert result.returncode == 0, result.stderr

    # current tag vs newest local tag, both printed...
    assert "v1.0.0" in result.stdout
    assert "v1.0.1" in result.stdout

    # ...and only read-only commands were ever invoked.
    log_lines = _shim_log(tmp_path, "check").splitlines()
    assert log_lines, "expected the read commands to be traced"
    for line in log_lines:
        assert line.startswith(("git describe", "git tag", "curl")), line


def test_dirty_tree_refuses_with_stash_hint(tmp_path: Path) -> None:
    result = _run_update(tmp_path, [], tag="dirty", git_dirty=" M docs/notes.md")
    assert result.returncode == 1

    combined = result.stdout + result.stderr
    assert "stash" in combined  # stash/commit hint

    # Refused before any other command: status is the only one logged.
    log_lines = _shim_log(tmp_path, "dirty").splitlines()
    assert len(log_lines) == 1, log_lines
    assert log_lines[0].startswith("git status --porcelain")


def test_health_failure_reports_recovery_details(tmp_path: Path) -> None:
    db = tmp_path / "muhideen.db"
    # Invoked from a *different* directory than the checkout: the backup
    # must still land in MUHIDEEN_BACKUP_DIR, not the caller's cwd.
    result = _run_update(
        tmp_path, [], tag="health", curl_exit=1, db=db, cwd=tmp_path / "work"
    )
    assert result.returncode == 1

    combined = result.stdout + result.stderr
    assert "backups/v1.0.0-" in combined  # backup path (tag + timestamp)
    assert "v1.0.0" in combined  # previous tag named for manual recovery
    assert "systemctl" in combined  # systemctl recovery hint

    # The backup really exists on disk before the checkout ran.
    backups = list((tmp_path / "backups").glob("v1.0.0-*.db"))
    assert backups, "VACUUM INTO backup must exist on health failure"


def test_build_vendor_dry_run_lists_locked_steps(tmp_path: Path) -> None:
    env = _shim_env(tmp_path, "build")
    result = subprocess.run(
        ["bash", str(BUILD_VENDOR), "--dry-run"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    out = result.stdout
    assert "uv export --no-dev --no-hashes" in out
    assert "pip download hatchling" in out  # decision 8: backend offline too
    assert "manylinux_2_17_aarch64" in out
    assert "manylinux_2_17_x86_64" in out
    assert "armv7l" not in out  # Pi 3 tier deferred to a follow-up issue

    # --dry-run executed nothing at all (no shim was ever called).
    assert _shim_log(tmp_path, "build") == ""
