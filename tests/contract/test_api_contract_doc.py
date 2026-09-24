"""Doc parity: docs/api-contract.md examples vs DTOs and fixtures (slice 1A-3).

Every fenced ```json example in the contract doc must validate against its
endpoint's DTO and equal the corresponding checked-in fixture — enforcing
"fixtures win on conflict" (`docs/api-contract.md:3`) and that doc +
fixtures update together.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from muhideen.api.dto import (
    AuthRequestDTO,
    AuthResponseDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    VersionDTO,
)

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "api-contract.md"
FIXTURES = ROOT / "api" / "fixtures"

SECTION_DTOS: dict[str, list[type[BaseModel]]] = {
    "GET /api/prayer-day": [PrayerDayDTO],
    "GET /api/next-event": [NextEventDTO],
    "GET /api/events": [],
    "POST /api/displays/heartbeat": [HeartbeatRequestDTO, HeartbeatResponseDTO],
    "GET /api/version": [VersionDTO],
    "GET /api/settings": [SettingsDTO],
    "PUT /api/settings": [SettingsDTO],
    "POST /api/auth/setup": [AuthRequestDTO, AuthResponseDTO],
    "POST /api/auth/login": [AuthRequestDTO, AuthResponseDTO],
    "POST /api/auth/logout": [AuthResponseDTO],
    "GET /api/auth/session": [SessionStatusDTO],
}
SECTION_FIXTURES: dict[str, list[str]] = {
    "GET /api/prayer-day": ["prayer-day.json"],
    "GET /api/next-event": ["next-event.json"],
    "GET /api/events": [],
    "POST /api/displays/heartbeat": [
        "heartbeat-request.json",
        "heartbeat-response.json",
    ],
    "GET /api/version": ["version.json"],
    "GET /api/settings": ["settings.json"],
    "PUT /api/settings": ["settings.json"],
    "POST /api/auth/setup": ["auth-request.json", "auth-response.json"],
    "POST /api/auth/login": ["auth-request.json", "auth-response.json"],
    "POST /api/auth/logout": ["auth-response.json"],
    "GET /api/auth/session": ["session.json"],
}


def _sections() -> dict[str, str]:
    """Split the doc on '## ' headings; keys have surrounding backticks stripped."""
    parts = DOC.read_text().split("\n## ")
    sections: dict[str, str] = {}
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        sections[title.strip().strip("`")] = body
    return sections


def _match_key(title: str) -> str | None:
    for key in SECTION_DTOS:
        if title.startswith(key):
            return key
    return None


def _json_blocks(section_body: str) -> list[Any]:
    blocks: list[Any] = []
    for chunk in section_body.split("```json")[1:]:
        blocks.append(json.loads(chunk.split("```", 1)[0]))
    return blocks


def _body_for(key: str) -> str:
    return next(body for title, body in _sections().items() if title.startswith(key))


def test_doc_json_examples_validate_against_dtos() -> None:
    for key, dtos in SECTION_DTOS.items():
        blocks = _json_blocks(_body_for(key))
        assert len(blocks) == len(dtos), f"{key}: expected {len(dtos)} JSON examples"
        for block, dto in zip(blocks, dtos, strict=True):
            dto.model_validate(block)


def test_unmapped_doc_sections_carry_no_json_examples() -> None:
    for title, body in _sections().items():
        if _match_key(title) is None:
            assert "```json" not in body, (
                f"section {title!r} has a JSON example but no DTO mapping"
            )


def test_doc_examples_match_fixtures() -> None:
    for key, fixture_names in SECTION_FIXTURES.items():
        if not fixture_names:
            continue
        blocks = _json_blocks(_body_for(key))
        assert len(blocks) == len(fixture_names)
        for block, name in zip(blocks, fixture_names, strict=True):
            assert block == json.loads((FIXTURES / name).read_text()), name


def test_time_synced_documented_in_next_event_and_state_examples() -> None:
    """FR-1.6 (slice 1A-8): the NTP flag rides next-event and every state frame."""
    example = _json_blocks(_body_for("GET /api/next-event"))[0]
    fixture = json.loads((FIXTURES / "next-event.json").read_text())
    assert example["time_synced"] is True
    assert fixture["time_synced"] is True
    stream = (FIXTURES / "events-stream.txt").read_text()
    state_data = [
        json.loads(line.removeprefix("data:").strip())
        for block in stream.split("\n\n")
        if "event: state" in block
        for line in block.splitlines()
        if line.startswith("data:")
    ]
    assert state_data, "events-stream.txt must sample SSE state frames"
    assert all(payload["time_synced"] is True for payload in state_data)
    assert "time_synced" in _body_for("GET /api/events")
