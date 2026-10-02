"""OpenAPI parity: create_app(deps) schema vs DTOs (slice 1A-7).

The OpenAPI document must be generated from the same Pydantic DTOs that the
fixtures validate against, so doc/fixtures/OpenAPI drift fails CI.
Handlers are real FastAPI sync defs over the engine; the SSE route documents
its payload schemas as an inline `anyOf` under a top-level `type: object`
(FastAPI's default `{"type": "string"}` must be overridden explicitly).
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel

from muhideen.adapters.sse_bus import SSEBus
from muhideen.api import (
    AuthRequestDTO,
    AuthResponseDTO,
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    IqamahRuleDTO,
    ManualDayDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)
from muhideen.api.app import (
    ActiveToggleDTO,
    AdhanAudioUploadDTO,
    AppDeps,
    BackupRestoreDTO,
    DisplayGroupUpdateDTO,
    DisplayRegisterDTO,
    DisplayUpdateDTO,
    PlaylistCreateDTO,
    PlaylistDTO,
    PlaylistImageUploadDTO,
    PlaylistItemDTO,
    create_app,
)
from muhideen.core.values import PrayerDay, Settings

pytestmark = pytest.mark.contract

KL = timezone(timedelta(hours=8))
_PINNED_NOW = datetime(2025, 10, 20, 12, 20, tzinfo=KL)


class _FakeClock:
    def now(self) -> datetime:
        return _PINNED_NOW

    def monotonic(self) -> float:
        return 0.0


class _FakeSettingsRepo:
    def load(self) -> Settings:
        raise NotImplementedError

    def save(self, settings: Settings) -> None:
        raise NotImplementedError


class _FakePrayerRepo:
    def get_day(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError

    def save_day(self, prayer_day: PrayerDay) -> None:
        raise NotImplementedError

    def save_day_unless_manual(self, prayer_day: PrayerDay) -> bool:
        raise NotImplementedError

    def delete_day(self, day: date, zone: str) -> bool:
        raise NotImplementedError

    def last_known(self, day: date, zone: str) -> PrayerDay | None:
        raise NotImplementedError


class _FakeDisplayRepo:
    def record_seen(self, display_id: str, ip: str | None) -> None:
        raise NotImplementedError

    def is_registered(self, display_id: str) -> bool:
        return True

    def flush(self) -> int:
        return 0


class _FakeUserRepo:
    def has_users(self) -> bool:
        return False

    def create_user(self, username: str, password: str) -> bool:
        raise NotImplementedError

    def verify(self, username: str, password: str) -> bool:
        return False


def _app() -> Any:
    deps = AppDeps(
        settings_repo=_FakeSettingsRepo(),  # type: ignore[arg-type]
        prayer_repo=_FakePrayerRepo(),  # type: ignore[arg-type]
        display_repo=_FakeDisplayRepo(),  # type: ignore[arg-type]
        user_repo=_FakeUserRepo(),  # type: ignore[arg-type]
        clock=_FakeClock(),  # type: ignore[arg-type]
        event_bus=SSEBus(),
    )
    return create_app(deps)


JSON_ENDPOINTS: dict[str, tuple[str, type[BaseModel]]] = {
    "/api/prayer-day": ("get", PrayerDayDTO),
    "/api/next-event": ("get", NextEventDTO),
    "/api/displays/heartbeat": ("post", HeartbeatResponseDTO),
    "/api/version": ("get", VersionDTO),
    # PUT /api/settings shares GET's $ref: one entry covers both methods.
    "/api/settings": ("get", SettingsDTO),
    # PUT /api/manual-day responds with the pinned day (PrayerDayDTO shape).
    "/api/manual-day": ("put", PrayerDayDTO),
    "/api/auth/setup": ("post", AuthResponseDTO),
    "/api/auth/login": ("post", AuthResponseDTO),
    "/api/auth/logout": ("post", AuthResponseDTO),
    "/api/auth/session": ("get", SessionStatusDTO),
    # Playlist detail: GET/PUT/PATCH share the path; one entry covers GET
    # while PUT/PATCH $refs are asserted explicitly below (like settings).
    "/api/playlists/{playlist_id}": ("get", PlaylistDTO),
}
ALL_DTOS = (
    ActiveToggleDTO,
    AdhanAudioUploadDTO,
    BackupRestoreDTO,
    DisplayGroupUpdateDTO,
    DisplayRegisterDTO,
    DisplayUpdateDTO,
    AuthRequestDTO,
    AuthResponseDTO,
    ConfigUpdateEventDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    IqamahRuleDTO,
    ManualDayDTO,
    NextEventDTO,
    PlaylistCreateDTO,
    PlaylistDTO,
    PlaylistImageUploadDTO,
    PlaylistItemDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    StateEventDTO,
    TickEventDTO,
    VersionDTO,
)


def _normalized(schema: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(schema).replace("#/$defs/", "#/components/schemas/")
    normalized: dict[str, Any] = json.loads(blob)
    normalized.pop("$defs", None)
    return normalized


def _strip_defaults(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_defaults(value)
            for key, value in node.items()
            if key != "default"
        }
    if isinstance(node, list):
        return [_strip_defaults(item) for item in node]
    return node


def test_api_package_exports_all_contract_dtos() -> None:
    for dto in ALL_DTOS:
        assert issubclass(dto, BaseModel)
    # The app factory lives in muhideen.api.app (not at the package root:
    # re-exporting it there reintroduces the views<->api import cycle).
    import muhideen.api.app as api_app

    assert api_app.create_app is create_app
    assert callable(create_app)


def test_openapi_declares_all_five_paths() -> None:
    paths = _app().openapi()["paths"]
    assert set(paths) == {
        "/api/prayer-day",
        "/api/next-event",
        "/api/events",
        "/api/displays/heartbeat",
        "/api/version",
        "/api/settings",
        "/api/manual-day",
        "/api/auth/setup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/playlists",
        "/api/playlists/preview",
        "/api/playlists/{playlist_id}",
        "/api/playlists/{playlist_id}/items",
        "/api/playlists/{playlist_id}/items/{sort_order}",
        "/api/adhan-audio",
        "/api/displays",
        "/api/displays/{display_id}",
        "/api/display-groups/{name}",
        "/api/backup/export",
        "/api/backup/restore",
        "/api/logs",
        "/display",
        "/admin",
        "/admin/login",
        "/admin/setup",
        "/admin/settings",
        "/admin/playlists",
    }


def test_openapi_response_schemas_match_dto_schemas() -> None:
    schema = _app().openapi()
    components = schema["components"]["schemas"]
    for path, (method, dto) in JSON_ENDPOINTS.items():
        responses = schema["paths"][path][method]["responses"]
        response_schema = responses["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{dto.__name__}"}
        # `_strip_defaults`: FastAPI strips Pydantic's `default:` markers
        # on DTOs that declare them (playlist shapes); older DTOs have
        # none, so stripping is a no-op for them.
        assert _strip_defaults(components[dto.__name__]) == _strip_defaults(
            _normalized(dto.model_json_schema())
        )
    # PUT shares GET's $ref: same SettingsDTO on the same path.
    put_schema = schema["paths"]["/api/settings"]["put"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert put_schema == {"$ref": "#/components/schemas/SettingsDTO"}
    heartbeat_body = schema["paths"]["/api/displays/heartbeat"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert heartbeat_body == {"$ref": "#/components/schemas/HeartbeatRequestDTO"}
    for setup_login in ("/api/auth/setup", "/api/auth/login"):
        body = schema["paths"][setup_login]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert body == {"$ref": "#/components/schemas/AuthRequestDTO"}
    settings_body = schema["paths"]["/api/settings"]["put"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert settings_body == {"$ref": "#/components/schemas/SettingsDTO"}
    manual_body = schema["paths"]["/api/manual-day"]["put"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert manual_body == {"$ref": "#/components/schemas/ManualDayDTO"}
    # Playlist detail: PUT/PATCH share GET's $ref (same PlaylistDTO).
    put_playlist = schema["paths"]["/api/playlists/{playlist_id}"]["put"]
    assert put_playlist["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PlaylistDTO"}
    assert put_playlist["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PlaylistDTO"
    }
    patch_playlist = schema["paths"]["/api/playlists/{playlist_id}"]["patch"]
    assert patch_playlist["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PlaylistDTO"}
    assert patch_playlist["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ActiveToggleDTO"
    }
    # Playlist creation is 201: response PlaylistDTO, body PlaylistCreateDTO.
    create_playlist = schema["paths"]["/api/playlists"]["post"]
    assert create_playlist["responses"]["201"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PlaylistDTO"}
    assert create_playlist["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PlaylistCreateDTO"
    }
    # Image upload + display registry: dict envelopes, DTO request bodies.
    upload_body = schema["paths"]["/api/playlists/{playlist_id}/items"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert upload_body == {"$ref": "#/components/schemas/PlaylistImageUploadDTO"}
    adhan_body = schema["paths"]["/api/adhan-audio"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert adhan_body == {"$ref": "#/components/schemas/AdhanAudioUploadDTO"}
    restore_body = schema["paths"]["/api/backup/restore"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert restore_body == {"$ref": "#/components/schemas/BackupRestoreDTO"}
    register_body = schema["paths"]["/api/displays"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert register_body == {"$ref": "#/components/schemas/DisplayRegisterDTO"}
    update_body = schema["paths"]["/api/displays/{display_id}"]["patch"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert update_body == {"$ref": "#/components/schemas/DisplayUpdateDTO"}
    group_body = schema["paths"]["/api/display-groups/{name}"]["patch"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert group_body == {"$ref": "#/components/schemas/DisplayGroupUpdateDTO"}
    for dto in (
        PlaylistDTO,
        PlaylistCreateDTO,
        PlaylistItemDTO,
        ActiveToggleDTO,
        PlaylistImageUploadDTO,
        AdhanAudioUploadDTO,
        BackupRestoreDTO,
        DisplayRegisterDTO,
        DisplayUpdateDTO,
        DisplayGroupUpdateDTO,
    ):
        # `_strip_defaults`: FastAPI strips Pydantic's `default:` markers
        # when emitting components (same reason as the SSE `anyOf` below).
        assert _strip_defaults(components[dto.__name__]) == _strip_defaults(
            _normalized(dto.model_json_schema())
        )
    for name in (
        "PrayerDayDTO",
        "NextEventDTO",
        "HeartbeatRequestDTO",
        "HeartbeatResponseDTO",
        "VersionDTO",
        "SettingsDTO",
        "IqamahRuleDTO",
        "ManualDayDTO",
        "AuthRequestDTO",
        "AuthResponseDTO",
        "SessionStatusDTO",
        "PlaylistDTO",
        "PlaylistCreateDTO",
        "PlaylistItemDTO",
        "ActiveToggleDTO",
        "PlaylistImageUploadDTO",
        "AdhanAudioUploadDTO",
        "BackupRestoreDTO",
        "DisplayRegisterDTO",
        "DisplayUpdateDTO",
        "DisplayGroupUpdateDTO",
    ):
        assert name in components


def test_openapi_query_params_match_doc() -> None:
    schema = _app().openapi()
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
    responses = _app().openapi()["paths"]["/api/events"]["get"]["responses"]
    assert set(responses["200"]["content"]) == {"text/event-stream"}


def test_events_openapi_documents_sse_payload_schemas() -> None:
    schema = _app().openapi()
    content = schema["paths"]["/api/events"]["get"]["responses"]["200"]["content"]
    payload_schema = content["text/event-stream"]["schema"]
    expected = [
        StateEventDTO.model_json_schema(),
        TickEventDTO.model_json_schema(),
        ConfigUpdateEventDTO.model_json_schema(),
    ]
    # Full-schema equality (no subtree carving, no excuses): FastAPI seeds
    # the response schema with {"type": "string"} and deep-merges the route's
    # `responses=` over it, so an emitted `type: string` would make the union
    # unsatisfiable; and `anyOf` (not `oneOf`) is required because a real
    # `tick` payload also matches StateEventDTO's state-only shape.
    # `_strip_defaults` drops the `default: None` that Pydantic adds on the
    # DTO side and FastAPI strips when emitting.
    assert _strip_defaults(payload_schema) == _strip_defaults(
        {"type": "object", "anyOf": expected}
    )
