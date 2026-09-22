"""OpenAPI parity: create_app() schema vs DTOs (slice 1A-3).

The OpenAPI document must be generated from the same Pydantic DTOs that the
fixtures validate against, so doc/fixtures/OpenAPI drift fails CI
(`PRD.md:225`). Stub handlers returning 501 document the seam: real wiring
lands in slice 1A-7.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from muhideen.api import (
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
    create_app,
)

pytestmark = pytest.mark.contract

JSON_ENDPOINTS: dict[str, tuple[str, type[BaseModel]]] = {
    "/api/prayer-day": ("get", PrayerDayDTO),
    "/api/next-event": ("get", NextEventDTO),
    "/api/displays/heartbeat": ("post", HeartbeatResponseDTO),
    "/api/version": ("get", VersionDTO),
}
ALL_DTOS = (
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)


def _normalized(schema: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(schema).replace("#/$defs/", "#/components/schemas/")
    normalized: dict[str, Any] = json.loads(blob)
    normalized.pop("$defs", None)
    return normalized


def test_api_package_exports_all_contract_dtos() -> None:
    for dto in ALL_DTOS:
        assert issubclass(dto, BaseModel)
    assert callable(create_app)


def test_openapi_declares_all_five_paths() -> None:
    paths = create_app().openapi()["paths"]
    assert {
        "/api/prayer-day",
        "/api/next-event",
        "/api/events",
        "/api/displays/heartbeat",
        "/api/version",
    } <= set(paths)


def test_openapi_response_schemas_match_dto_schemas() -> None:
    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    for path, (method, dto) in JSON_ENDPOINTS.items():
        responses = schema["paths"][path][method]["responses"]
        response_schema = responses["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{dto.__name__}"}
        assert components[dto.__name__] == _normalized(dto.model_json_schema())
    heartbeat_body = schema["paths"]["/api/displays/heartbeat"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert heartbeat_body == {"$ref": "#/components/schemas/HeartbeatRequestDTO"}
    for name in (
        "PrayerDayDTO",
        "NextEventDTO",
        "HeartbeatRequestDTO",
        "HeartbeatResponseDTO",
        "VersionDTO",
    ):
        assert name in components


def test_openapi_query_params_match_doc() -> None:
    schema = create_app().openapi()
    prayer_day_params = {
        param["name"]
        for param in schema["paths"]["/api/prayer-day"]["get"].get("parameters", [])
        if param["in"] == "query"
    }
    assert prayer_day_params == {"date", "zone"}
    next_event_params = {
        param["name"]
        for param in schema["paths"]["/api/next-event"]["get"].get("parameters", [])
        if param["in"] == "query"
    }
    assert next_event_params == {"now"}


def test_events_route_declares_text_event_stream() -> None:
    responses = create_app().openapi()["paths"]["/api/events"]["get"]["responses"]
    assert "text/event-stream" in responses["200"]["content"]


def test_stub_endpoints_return_501() -> None:
    client = TestClient(create_app())
    assert (
        client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
        ).status_code
        == 501
    )
    assert (
        client.get("/api/next-event", params={"now": "2025-10-20T12:20:00"}).status_code
        == 501
    )
    assert client.get("/api/events").status_code == 501
    assert client.get("/api/version").status_code == 501
    assert (
        client.post("/api/displays/heartbeat", json={"id": "HALL-01"}).status_code
        == 501
    )
