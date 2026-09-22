"""API layer: executable contract (Pydantic DTOs) + FastAPI app skeleton."""

from muhideen.api.app import create_app
from muhideen.api.dto import (
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)

__all__ = [
    "ConfigUpdateEventDTO",
    "HeartbeatRequestDTO",
    "HeartbeatResponseDTO",
    "NextEventDTO",
    "PrayerDayDTO",
    "StateEventDTO",
    "TickEventDTO",
    "VersionDTO",
    "create_app",
]
