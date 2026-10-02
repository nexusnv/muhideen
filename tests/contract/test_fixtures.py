"""Contract parity: Pydantic DTOs vs checked-in api/fixtures (slice 1A-3).

Round-trip both directions: fixture -> DTO -> dump equals the fixture dict
(exact key set via extra="forbid"), and domain value objects -> DTO equals
the fixture. Negative cases pin the wire format: uppercase state, HH:MM
times, tz-aware ISO8601 datetimes, no extra fields.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from muhideen.api.app import (
    ActiveToggleDTO,
    AdhanAudioUploadDTO,
    BackupRestoreDTO,
    DisplayGroupUpdateDTO,
    DisplayRegisterDTO,
    DisplayUpdateDTO,
    PlaylistCreateDTO,
    PlaylistDTO,
    PlaylistImageUploadDTO,
    PlaylistItemDTO,
)
from muhideen.api.dto import (
    AuthRequestDTO,
    AuthResponseDTO,
    HeartbeatRequestDTO,
    HeartbeatResponseDTO,
    ManualDayDTO,
    NextEventDTO,
    PrayerDayDTO,
    SessionStatusDTO,
    SettingsDTO,
    VersionDTO,
)
from muhideen.core.errors import SyncError
from muhideen.core.values import (
    MarkerName,
    NextEvent,
    PrayerDay,
    PrayerState,
    ScheduleSource,
)
from muhideen.domain.ordering import ensure_ordered

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[2] / "api" / "fixtures"
KL = timezone(timedelta(hours=8))


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_prayer_day_fixture_round_trips() -> None:
    payload = _load("prayer-day.json")
    dto = PrayerDayDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_next_event_fixture_round_trips() -> None:
    payload = _load("next-event.json")
    dto = NextEventDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_state_requires_uppercase_wire_value() -> None:
    payload = dict(_load("next-event.json"), state="iqamah_countdown")
    with pytest.raises(ValidationError):
        NextEventDTO.model_validate(payload)


def test_times_require_hh_mm_format() -> None:
    payload = _load("prayer-day.json")
    payload["prayers"] = {**payload["prayers"], "fajr": "05:45:00"}
    with pytest.raises(ValidationError):
        PrayerDayDTO.model_validate(payload)
    payload = _load("prayer-day.json")
    payload["boundaries"] = {**payload["boundaries"], "imsak": "05:35:00"}
    with pytest.raises(ValidationError):
        PrayerDayDTO.model_validate(payload)


def test_prayer_day_prayers_reject_boundary_key() -> None:
    """extra=forbid makes the class split executable on the wire."""
    payload = _load("prayer-day.json")
    payload["prayers"]["syuruq"] = "06:55"
    with pytest.raises(ValidationError):
        PrayerDayDTO.model_validate(payload)


def test_prayer_day_requires_boundaries() -> None:
    payload = _load("prayer-day.json")
    payload.pop("boundaries")
    with pytest.raises(ValidationError):
        PrayerDayDTO.model_validate(payload)


def test_next_prayer_rejects_boundary_marker() -> None:
    """next_prayer is narrowed to Prayer Time Markers (PRD FR-1.7)."""
    with pytest.raises(ValidationError):
        NextEventDTO.model_validate(
            dict(_load("next-event.json"), next_prayer="syuruq")
        )


def test_next_boundary_rejects_prayer_marker() -> None:
    with pytest.raises(ValidationError):
        NextEventDTO.model_validate(
            dict(_load("next-event.json"), next_boundary="dhuhr")
        )


def test_naive_datetime_rejected() -> None:
    payload = dict(_load("next-event.json"), now="2025-10-20T12:20:00")
    with pytest.raises(ValidationError):
        NextEventDTO.model_validate(payload)


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        NextEventDTO.model_validate(dict(_load("next-event.json"), extra=1))
    with pytest.raises(ValidationError):
        PrayerDayDTO.model_validate(dict(_load("prayer-day.json"), extra=1))


def test_next_event_from_domain_matches_fixture() -> None:
    event = NextEvent(
        state=PrayerState.IQAMAH_COUNTDOWN,
        now=datetime(2025, 10, 20, 12, 20, tzinfo=KL),
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15, tzinfo=KL),
        iqamah_at=datetime(2025, 10, 20, 12, 30, tzinfo=KL),
        dim_until=datetime(2025, 10, 20, 12, 50, tzinfo=KL),
        stale=False,
        next_boundary=MarkerName.IMSAK,
        boundary_at=datetime(2025, 10, 21, 5, 35, tzinfo=KL),
    )
    assert NextEventDTO.from_domain(event).model_dump(mode="json") == _load(
        "next-event.json"
    )


def test_prayer_day_from_domain_matches_fixture() -> None:
    day = PrayerDay(
        date=date(2025, 10, 20),
        zone="SGR01",
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 20, 1, 0, tzinfo=KL),
    )
    assert PrayerDayDTO.from_domain(
        day, stale=False, hijri_date="1447-04-28"
    ).model_dump(mode="json") == _load("prayer-day.json")


def test_manual_day_fixture_round_trips() -> None:
    payload = _load("manual-day.json")
    dto = ManualDayDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_manual_day_to_prayer_day_matches_prayer_day_shape() -> None:
    dto = ManualDayDTO.model_validate(_load("manual-day.json"))
    day = dto.to_prayer_day(zone="SGR01", now=datetime(2025, 10, 20, 1, 0, tzinfo=KL))
    assert day.source is ScheduleSource.MANUAL
    assert day.zone == "SGR01"
    assert PrayerDayDTO.from_domain(
        day, stale=True, hijri_date="1447-04-28"
    ).model_dump(mode="json") == {
        "date": "2025-10-20",
        "zone": "SGR01",
        "prayers": {
            "fajr": "05:45",
            "dhuhr": "12:15",
            "asr": "15:30",
            "maghrib": "18:05",
            "isha": "19:25",
        },
        "boundaries": {"imsak": "05:35", "syuruq": "06:55", "dhuha": "07:25"},
        "source": "manual",
        "stale": True,
        "hijri_date": "1447-04-28",
    }


def test_manual_day_rejects_misordered_markers() -> None:
    payload = _load("manual-day.json")
    payload["asr"], payload["maghrib"] = payload["maghrib"], payload["asr"]
    dto = ManualDayDTO.model_validate(payload)
    with pytest.raises(SyncError):
        ensure_ordered(
            dto.to_prayer_day(zone="SGR01", now=datetime(2025, 10, 20, 1, 0, tzinfo=KL))
        )


def test_version_fixture_round_trips() -> None:
    payload = _load("version.json")
    dto = VersionDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_heartbeat_request_fixture_round_trips() -> None:
    payload = _load("heartbeat-request.json")
    dto = HeartbeatRequestDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_heartbeat_response_fixture_round_trips() -> None:
    payload = _load("heartbeat-response.json")
    dto = HeartbeatResponseDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_heartbeat_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        HeartbeatRequestDTO.model_validate({"id": ""})


def test_version_rejects_unknown_api_value() -> None:
    with pytest.raises(ValidationError):
        VersionDTO.model_validate({"version": "0.1.0", "api": "v2"})


def test_settings_fixture_round_trips() -> None:
    payload = _load("settings.json")
    dto = SettingsDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_auth_fixtures_round_trip() -> None:
    request_payload = _load("auth-request.json")
    request_dto = AuthRequestDTO.model_validate(request_payload)
    assert request_dto.model_dump(mode="json") == request_payload
    response_payload = _load("auth-response.json")
    response_dto = AuthResponseDTO.model_validate(response_payload)
    assert response_dto.model_dump(mode="json") == response_payload


def test_session_fixture_round_trips() -> None:
    payload = _load("session.json")
    dto = SessionStatusDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_playlist_fixture_round_trips() -> None:
    payload = _load("playlist.json")
    dto = PlaylistDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_playlist_create_fixture_round_trips() -> None:
    payload = _load("playlist-create.json")
    dto = PlaylistCreateDTO.model_validate(payload)
    assert dto.model_dump(mode="json") == payload


def test_playlist_from_domain_matches_fixture() -> None:
    from muhideen.core.values import Playlist, PlaylistItem

    playlist = Playlist(
        id="p1",
        title="Title p1",
        active=True,
        window_start="09:00",
        window_end="18:00",
        items=(PlaylistItem(image_path="a.jpg", duration_s=10, sort_order=0),),
    )
    assert PlaylistDTO.from_domain(playlist).model_dump(mode="json") == _load(
        "playlist.json"
    )


def test_playlist_toggle_and_item_fixtures_round_trip() -> None:
    toggle = ActiveToggleDTO.model_validate(_load("active-toggle.json"))
    assert toggle.model_dump(mode="json") == _load("active-toggle.json")
    upload = PlaylistImageUploadDTO.model_validate(_load("playlist-image-upload.json"))
    assert upload.model_dump(mode="json") == _load("playlist-image-upload.json")
    item = PlaylistItemDTO.model_validate(_load("playlist-item.json"))
    assert item.model_dump(mode="json") == _load("playlist-item.json")


def test_adhan_audio_upload_fixture_round_trips() -> None:
    upload = AdhanAudioUploadDTO.model_validate(_load("adhan-audio-upload.json"))
    assert upload.model_dump(mode="json") == _load("adhan-audio-upload.json")


def test_display_registry_fixtures_round_trip() -> None:
    register = DisplayRegisterDTO.model_validate(_load("display-register.json"))
    assert register.model_dump(mode="json") == _load("display-register.json")
    update = DisplayUpdateDTO.model_validate(_load("display-update.json"))
    assert update.model_dump(mode="json") == _load("display-update.json")
    group = DisplayGroupUpdateDTO.model_validate(_load("display-group-update.json"))
    assert group.model_dump(mode="json") == _load("display-group-update.json")


def test_backup_restore_fixture_round_trips() -> None:
    payload = _load("backup-restore.json")
    dto = BackupRestoreDTO.model_validate(payload["request"])
    assert dto.model_dump(mode="json") == payload["request"]
    assert payload["response"] == {"ok": True}


def test_backup_restore_rejects_empty_archive() -> None:
    with pytest.raises(ValidationError):
        BackupRestoreDTO.model_validate({"archive_base64": ""})


def test_logs_fixture_shapes() -> None:
    payload = _load("logs.json")
    assert set(payload) == {"available", "unavailable"}
    assert payload["available"]["available"] is True
    assert isinstance(payload["available"]["lines"], list)
    assert payload["unavailable"]["available"] is False
    assert isinstance(payload["unavailable"]["hint"], str)


def test_all_contract_surfaces_have_fixtures() -> None:
    for name in (
        "prayer-day.json",
        "next-event.json",
        "heartbeat-request.json",
        "heartbeat-response.json",
        "version.json",
        "settings.json",
        "auth-request.json",
        "auth-response.json",
        "session.json",
        "playlist.json",
        "playlist-create.json",
        "active-toggle.json",
        "playlist-image-upload.json",
        "playlist-item.json",
        "adhan-audio-upload.json",
        "display-register.json",
        "display-update.json",
        "display-group-update.json",
        "manual-day.json",
        "backup-restore.json",
        "logs.json",
    ):
        _load(name)
    assert (FIXTURES / "events-stream.txt").read_text().strip()


def test_settings_theme_fixture_round_trips() -> None:
    from muhideen.core.values import Settings, ThemeSettings

    payload = _load("settings.json")
    assert payload["theme"] == {
        "palette": "classic-green",
        "font": "outfit",
        "countdown_style": "boxes",
        "clock_format": "24h-seconds",
        "hijri_form": "long",
        "boundary_strip": "show",
        "density": "comfortable",
    }
    dto = SettingsDTO.model_validate(payload)
    assert dto.model_dump(mode="json")["theme"] == payload["theme"]
    assert dto.to_domain().theme == ThemeSettings()
    assert (
        SettingsDTO.from_domain(
            Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
        ).model_dump(mode="json")["theme"]
        == payload["theme"]
    )


def test_settings_rejects_unknown_theme_knob() -> None:
    from pydantic import ValidationError as _ValidationError

    payload = _load("settings.json")
    payload["theme"] = {**payload["theme"], "palette": "neon"}
    with pytest.raises(_ValidationError):
        SettingsDTO.model_validate(payload)
    payload = _load("settings.json")
    payload["theme"] = {**payload["theme"], "clock_format": "13h"}
    with pytest.raises(_ValidationError):
        SettingsDTO.model_validate(payload)
