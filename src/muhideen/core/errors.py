"""Core errors: typed failure modes for schedule resolution and sync."""


class MuhideenError(Exception):
    """Base exception for all Muhideen errors."""


class ContractError(MuhideenError):
    """Malformed contract payload or version mismatch."""


class ConfigError(MuhideenError):
    """Invalid installation settings (zone, offsets, durations)."""


class ScheduleError(MuhideenError):
    """A schedule could not be resolved; carries context for banners."""

    def __init__(self, message: str, zone: str = "", date: str = "") -> None:
        self.zone = zone
        self.date = date
        super().__init__(message)


class SyncError(MuhideenError):
    """A schedule source fetch failed; carries context for retries."""

    def __init__(self, message: str, zone: str = "", date: str = "") -> None:
        self.zone = zone
        self.date = date
        super().__init__(message)
