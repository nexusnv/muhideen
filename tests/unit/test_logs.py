"""Service log reader: journalctl tail for the admin UI (issue #41)."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.unit


def test_read_logs_returns_runner_lines() -> None:
    from muhideen.adapters.logs import read_logs

    def fake_runner(cmd: list[str]) -> str:
        assert cmd[:3] == ["journalctl", "-u", "muhideen"]
        assert cmd[-2:] == ["-n", "100"]
        return "line one\nline two\nline three\n"

    assert read_logs(lines=100, runner=fake_runner) == {
        "available": True,
        "lines": ["line one", "line two", "line three"],
    }


@pytest.mark.parametrize(
    "error",
    [OSError("no journal"), subprocess.CalledProcessError(1, ["journalctl"])],
    ids=["os-error", "called-process-error"],
)
def test_read_logs_unavailable_on_runner_failure(error: Exception) -> None:
    from muhideen.adapters.logs import read_logs

    def failing_runner(cmd: list[str]) -> str:
        raise error

    result = read_logs(runner=failing_runner)
    assert result["available"] is False
    assert isinstance(result["hint"], str) and "journalctl" in result["hint"]


def test_read_logs_clamps_line_count() -> None:
    from muhideen.adapters.logs import read_logs

    seen: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> str:
        seen.append(cmd)
        return "only\n"

    assert read_logs(lines=0, runner=fake_runner) == {
        "available": True,
        "lines": ["only"],
    }
    assert seen[-1][-2:] == ["-n", "1"]

    assert read_logs(lines=5000, runner=fake_runner) == {
        "available": True,
        "lines": ["only"],
    }
    assert seen[-1][-2:] == ["-n", "1000"]
