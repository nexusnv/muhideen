"""Service log reader: journalctl tail for the admin UI (issue #41)."""

from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.unit


def _journal(*messages: str) -> str:
    """Fake journalctl --output=json stdout: one object per entry."""
    return "\n".join(json.dumps({"MESSAGE": msg}) for msg in messages) + "\n"


def test_read_logs_returns_runner_lines() -> None:
    from muhideen.adapters.logs import read_logs

    def fake_runner(cmd: list[str]) -> str:
        assert cmd[:3] == ["journalctl", "-u", "muhideen"]
        assert "--quiet" in cmd
        assert "--output=json" in cmd
        assert cmd[-2:] == ["-n", "100"]
        return _journal("line one", "line two", "line three")

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


@pytest.mark.parametrize("output", ["", "   \n  \n"], ids=["empty", "whitespace"])
def test_read_logs_unavailable_on_blank_stdout(output: str) -> None:
    from muhideen.adapters.logs import read_logs

    result = read_logs(runner=lambda cmd: output)
    assert result["available"] is False
    assert isinstance(result["hint"], str) and "journalctl" in result["hint"]


def test_read_logs_counts_entries_not_raw_lines() -> None:
    from muhideen.adapters.logs import read_logs

    def fake_runner(cmd: list[str]) -> str:
        assert cmd[-2:] == ["-n", "2"]
        # One entry carries an embedded newline; a corrupt line is skipped.
        return (
            _journal("first", "multi\nline entry", "third")
            + "not json at all\n"
            + _journal("fourth", "fifth")
        )

    assert read_logs(lines=2, runner=fake_runner) == {
        "available": True,
        "lines": ["fourth", "fifth"],
    }


def test_read_logs_preserves_embedded_newlines() -> None:
    from muhideen.adapters.logs import read_logs

    result = read_logs(lines=10, runner=lambda cmd: _journal("a\nb", "c"))
    assert result == {"available": True, "lines": ["a\nb", "c"]}


def test_read_logs_clamps_line_count() -> None:
    from muhideen.adapters.logs import read_logs

    seen: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> str:
        seen.append(cmd)
        return _journal("only")

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
