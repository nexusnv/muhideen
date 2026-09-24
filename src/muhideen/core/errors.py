"""Core errors: typed failure modes for schedule resolution and sync."""


class MuhideenError(Exception):
    """Base exception for all Muhideen errors."""


class ContractError(MuhideenError):
    """Malformed contract payload or version mismatch."""


class ConfigError(MuhideenError):
    """Invalid installation settings (zone, offsets, durations)."""


class SettingsNotInitializedError(ConfigError):
    """Identity settings are missing (first boot, setup wizard not run)."""


class ScheduleError(MuhideenError):
    """A schedule could not be resolved; carries context for banners."""

    def __init__(self, message: str, zone: str = "", date: str = "") -> None:
        """Carry the failing zone/date alongside the schedule message."""
        self.zone = zone
        self.date = date
        super().__init__(message)


class SyncError(MuhideenError):
    """A schedule source fetch failed; carries context for retries."""

    def __init__(self, message: str, zone: str = "", date: str = "") -> None:
        """Carry the failing zone/date alongside the sync message."""
        self.zone = zone
        self.date = date
        super().__init__(message)
