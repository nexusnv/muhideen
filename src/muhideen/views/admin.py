"""Admin context builders: settings DTO to Jinja context (frontend-owned).

Imports ``api.dto`` + ``core`` only — never ``domain``/``engine``/adapters.
"""

from __future__ import annotations

from muhideen.api.dto import SettingsDTO


def login_context() -> dict[str, object]:
    """Context for the login page (no data needed)."""
    return {"lang": "bm"}


def setup_context() -> dict[str, object]:
    """Context for the first-boot wizard (no data needed)."""
    return {"lang": "bm"}


def settings_context(
    *, settings: SettingsDTO, rules_json: str, qr_data_uri: str, fallback_url: str
) -> dict[str, object]:
    """Prefilled settings page plus QR block (LAN URL only, never secrets)."""
    return {
        "lang": "bm",
        "current": settings,
        "rules_json": rules_json,
        "qr_data_uri": qr_data_uri,
        "fallback_url": fallback_url,
    }
