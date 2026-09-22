"""FastAPI app skeleton: contract routes with stub handlers (slice 1A-3).

This module exists so OpenAPI is generated from the same Pydantic DTOs the
fixtures validate against — drift between the two fails contract tests.
Handler bodies, auth, rate limits, and SSE streaming land in slice 1A-7;
until then every route answers 501.
"""

from __future__ import annotations

from datetime import date, datetime
from importlib.metadata import version as package_version

from fastapi import FastAPI, HTTPException
from starlette.responses import StreamingResponse

from muhideen.api.dto import (
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    VersionDTO,
)

_STUB_DETAIL = "handler wired in slice 1A-7"


class EventStreamResponse(StreamingResponse):
    """SSE responses: content type text/event-stream, declared in OpenAPI."""

    media_type = "text/event-stream"


def create_app() -> FastAPI:
    app = FastAPI(title="muhideen", version=package_version("muhideen"))

    @app.get("/api/prayer-day", response_model=PrayerDayDTO)
    def prayer_day(date: date, zone: str) -> PrayerDayDTO:
        raise HTTPException(status_code=501, detail=_STUB_DETAIL)

    @app.get("/api/next-event", response_model=NextEventDTO)
    def next_event(now: datetime) -> NextEventDTO:
        raise HTTPException(status_code=501, detail=_STUB_DETAIL)

    @app.get("/api/events", response_class=EventStreamResponse)
    def events() -> None:
        """SSE stream of state/tick/config-update events."""
        raise HTTPException(status_code=501, detail=_STUB_DETAIL)

    @app.post(
        "/api/displays/heartbeat",
        response_model=HeartbeatResponseDTO,
    )
    def heartbeat(payload: HeartbeatRequestDTO) -> HeartbeatResponseDTO:
        raise HTTPException(status_code=501, detail=_STUB_DETAIL)

    @app.get("/api/version", response_model=VersionDTO)
    def version() -> VersionDTO:
        raise HTTPException(status_code=501, detail=_STUB_DETAIL)

    return app
