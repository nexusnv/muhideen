"""API layer: executable contract (Pydantic DTOs) + FastAPI app."""

from muhideen.api.app import AppDeps, create_app, create_production_app
from muhideen.api.dto import (
    AuthRequestDTO,
    AuthResponseDTO,
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    IqamahRuleDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)

__all__ = [
    "AppDeps",
    "AuthRequestDTO",
    "AuthResponseDTO",
    "ConfigUpdateEventDTO",
    "HeartbeatRequestDTO",
    "HeartbeatResponseDTO",
    "IqamahRuleDTO",
    "NextEventDTO",
    "PrayerDayDTO",
    "SessionStatusDTO",
    "SettingsDTO",
    "StateEventDTO",
    "TickEventDTO",
    "VersionDTO",
    "create_app",
    "create_production_app",
]
