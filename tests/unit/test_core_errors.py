"""Guards for core errors (slice 1A-1, Task 4)."""

import pytest

from muhideen.core.errors import (
    ConfigError,
    ContractError,
    MuhideenError,
    ScheduleError,
    SyncError,
)


@pytest.mark.unit
def test_hierarchy() -> None:
    for error in (ContractError, ConfigError, ScheduleError, SyncError):
        assert issubclass(error, MuhideenError)
        assert isinstance(error("boom"), MuhideenError)


@pytest.mark.unit
def test_context_carried() -> None:
    error = ScheduleError("no data", zone="SGR01", date="2025-10-20")
    assert error.zone == "SGR01"
    assert error.date == "2025-10-20"
    assert str(error) == "no data"
    sync_error = SyncError("timeout", zone="WKP01")
    assert (sync_error.zone, sync_error.date) == ("WKP01", "")
