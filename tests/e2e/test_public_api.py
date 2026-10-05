"""E2E public surface: prayer-day, next-event, heartbeat, version (slice 1A-7)."""

from __future__ import annotations

from dataclasses import replace
from importlib.metadata import version as package_version
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from muhideen.api.app import create_app

pytestmark = pytest.mark.e2e


def _seed_settings(surface: SimpleNamespace, **overrides: Any) -> None:
    from muhideen.core.values import Settings

    base: dict[str, Any] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
    }
    base.update(overrides)
    surface.settings_repo.save(Settings(**base))  # type: ignore[arg-type]


def test_prayer_day_returns_resolved_day_for_seeded_settings(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2025-10-20"
    assert payload["zone"] == "SGR01"
    assert payload["source"] == "calc"


def test_prayer_day_without_resolvable_schedule_is_404(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 404


def test_prayer_day_unknown_zone_is_404(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """An unconfigured zone must 404 even when calc coordinates exist."""
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "ZZZ99"}
    )
    assert response.status_code == 404
    assert "ZZZ99" in response.json()["detail"]


def test_prayer_day_without_config_file_is_503(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """A missing config file is the file-config unconfigured branch (503)."""
    surface.config_path.unlink()
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 503


def test_next_event_accepts_tz_aware_now(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 200
    assert response.json()["state"] in {
        "NORMAL",
        "PRE_ADHAN",
        "ADHAN",
        "IQAMAH_COUNTDOWN",
        "SALAH_DIM",
    }


def test_next_event_rejects_naive_now_with_422(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get("/api/next-event", params={"now": "2025-10-20T12:20:00"})
    assert response.status_code == 422
    assert "offset" in response.text.lower()


def test_next_event_reports_time_synced_true_by_default(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """FR-1.6: an app with no probe wired reports synced (dev default)."""
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 200
    assert response.json()["time_synced"] is True


class _UnsyncedProbe:
    """File-local TimeSyncProbe double: NTP reports unsynchronised."""

    def synchronized(self) -> bool:
        return False


def test_unsynced_probe_reports_time_synced_false(
    surface: SimpleNamespace,
) -> None:
    """TIME UNSYNCED path: a probe saying no flows to the next-event payload."""
    _seed_settings(surface, lat=3.07, lon=101.69)
    app = create_app(replace(surface.deps, time_sync=_UnsyncedProbe()))
    with TestClient(app) as client:
        response = client.get(
            "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
        )
    assert response.status_code == 200
    assert response.json()["time_synced"] is False


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"date": "not-a-date", "zone": "SGR01"}, 422),
        ({"date": "2025-10-20", "zone": ""}, 422),
    ],
)
def test_malformed_date_and_zone_inputs_are_422(
    surface: SimpleNamespace,
    client: TestClient,
    params: dict[str, str],
    expected: int,
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get("/api/prayer-day", params=params)
    assert response.status_code == expected


def test_version_reports_package_version(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"version": package_version("muhideen"), "api": "v1"}


def test_heartbeat_route_removed(surface: SimpleNamespace, client: TestClient) -> None:
    """v1.0 drops device registration: heartbeats are no longer accepted."""
    response = client.post("/api/displays/heartbeat", json={"id": "HALL-01"})
    # 404: the whole /api/displays/* registry surface is gone with the
    # database stack, so the old {"ok": True, "registered": ...} envelope
    # cannot come back under any method.
    assert response.status_code == 404
    assert "registered" not in response.json()


def _seed_early_fixed_iqamah(surface: SimpleNamespace) -> None:
    from datetime import time

    from muhideen.core.values import DEFAULT_IQAMAH_RULES, IqamahRule, MarkerName

    rules = tuple(
        IqamahRule(prayer=MarkerName.DHUHR, mode="fixed", fixed_time=time(0, 10))
        if rule.prayer is MarkerName.DHUHR
        else rule
        for rule in DEFAULT_IQAMAH_RULES
    )
    _seed_settings(surface, lat=3.07, lon=101.69, iqamah_rules=rules)


def test_fixed_iqamah_at_or_before_adhan_is_503_on_next_event(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_early_fixed_iqamah(surface)
    response = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    )
    assert response.status_code == 503
    assert "at or before adhan" in response.json()["detail"]


def test_display_with_early_fixed_iqamah_is_503(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_early_fixed_iqamah(surface)
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 503


def test_prayer_day_carries_hijri_date(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 200
    assert response.json()["hijri_date"] == "1447-04-28"


def test_prayer_day_hijri_date_null_outside_library_range(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=3.07, lon=101.69)
    response = client.get(
        "/api/prayer-day", params={"date": "1900-01-01", "zone": "SGR01"}
    )
    assert response.status_code == 200
    assert response.json()["hijri_date"] is None


def test_503_detail_scrubs_absolute_config_path(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """Wire 503s must not disclose the server's filesystem layout."""
    surface.config_path.unlink()
    response = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert str(surface.config_path) not in detail
    assert "/etc/" not in detail and "/tmp/" not in detail


def test_media_mount_serves_operator_dropped_adhan(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """The configured media dir (outside the static root) is served at /media."""
    blob = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
    (surface.media_dir / "adhan.mp3").write_bytes(blob)
    response = client.get("/media/adhan.mp3")
    assert response.status_code == 200
    assert response.content == blob


def test_media_mount_blocks_traversal(
    surface: SimpleNamespace, client: TestClient
) -> None:
    """Above-root escapes through the /media mount must not resolve."""
    response = client.get("/media/%2e%2e/muhideen.json")
    assert response.status_code == 404
