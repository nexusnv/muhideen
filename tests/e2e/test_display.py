"""E2E display surface: mockup regions, 12h timetable, countdown (redesign)."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from muhideen.core.values import PrayerDay, ScheduleSource

pytestmark = pytest.mark.e2e

PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")


def _seed_settings(surface: SimpleNamespace, **overrides: Any) -> None:
    from muhideen.core.values import Settings

    base: dict[str, Any] = {
        "masjid_name": "Masjid Test",
        "zone": "SGR01",
        "hijri_offset": 0,
        "lat": 3.07,
        "lon": 101.69,
    }
    base.update(overrides)
    surface.settings_repo.save(Settings(**base))  # type: ignore[arg-type]


def _advance_to(surface: SimpleNamespace, target: datetime) -> None:
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone(timedelta(hours=8)))
    delta = (target - surface.clock.now()).total_seconds()
    assert delta >= 0, "walkthrough only moves forward"
    surface.clock.advance(delta)


def _seed_jakim_day(surface: SimpleNamespace) -> None:
    """Seed a fresh cached JAKIM row so no banner fires (banner-absent case)."""
    surface.prayer_repo.save_day(
        PrayerDay(
            date=surface.clock.now().date(),
            zone="SGR01",
            imsak=time(5, 38),
            fajr=time(5, 48),
            syuruq=time(6, 58),
            dhuha=time(7, 28),
            dhuhr=time(13, 0),
            asr=time(16, 35),
            maghrib=time(19, 8),
            isha=time(20, 28),
            source=ScheduleSource.JAKIM,
            fetched_at=surface.clock.now() - timedelta(hours=1),
        )
    )


def test_display_renders_mockup_regions(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    # Hidden JS anchor + state class on the body.
    assert 'id="hero-clock"' in html
    assert "hidden data-now=" in html
    assert "state-normal" in html
    # Timetable: header + one row per prayer, exactly one highlighted.
    for header in ("Prayer</div>", "Adhan</div>", "Iqomah</div>"):
        assert header in html
    for key in PRAYER_KEYS:
        assert f'id="row-{key}"' in html
    assert html.count("prayer-row current") == 1
    # 12h adhan + iqamah cells (5 rows + bounds strip).
    assert (
        len(re.findall(r">\d{1,2}:\d{2}<small class=\"period\">(AM|PM)</small>", html))
        >= 10
    )
    # Clock block: server 12h fallback with blinking-colon span + period.
    assert 'id="live-clock"' in html
    assert '<span class="clock-h">12</span>' in html
    assert '<span class="colon">:</span><span class="clock-m">20</span>' in html
    assert ">PM</span>" in html
    # Dates + mosque block.
    assert "20 October 2025" in html
    assert "1447" in html  # Hijri date present (calc 2026 day + offset 0)
    assert "Masjid Test" in html
    assert "SGR01" in html
    # Bounds strip present; countdown block blank in NORMAL (outside window).
    assert 'id="bounds"' in html
    assert 'id="countdown-label"' not in html
    assert 'id="countdown"' not in html


def test_display_timetable_english_only(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for token in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"):
        assert token in html
    # Backend still computes ar/bm (unit-guarded) but the screen is EN-only.
    for token in ("الفجر", "المغرب", "Subuh", "Zohor", "Jumaat"):
        assert token not in html


def test_display_bounds_english_only(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bound-syuruq"' in html
    bound_div = html.split('id="bound-syuruq"')[1].split("</div>")[0]
    assert "Syuruq" in bound_div
    assert "Syuruk" not in bound_div
    assert "الشروق" not in bound_div


def test_display_hides_disabled_imsak(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, imsak_offset_min=0)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bound-imsak"' not in html
    assert 'id="bound-syuruq"' in html


def test_display_calc_fallback_banner(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "CALC" in html  # seeded coords, no cache: calc source is stale
    assert 'id="banners"' in html


def test_display_omits_banner_strip_without_banners(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_jakim_day(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="banners"' not in html
    assert "CALC" not in html
    assert 'id="row-dhuhr"' in html  # normal layout otherwise intact


def test_display_boundary_next_marker(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, boundary_countdown=True)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert html.count("additional-time next") == 1
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "additional-time next" not in html


def test_display_before_setup_is_503_slate(
    surface: SimpleNamespace, client: TestClient
) -> None:
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 503
    assert 'id="slate"' in response.text


def test_display_without_resolvable_schedule_is_404_slate(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface, lat=None, lon=None)
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 404
    assert 'id="slate"' in response.text


def test_display_carries_realtime_data_attrs(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for attr in ("data-now=", "data-state=", "data-next=", "data-adhan="):
        assert attr in html


def test_display_carries_tzoffset_for_fixed_offset_clock(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'data-tzoffset="480"' in html
    assert "data-tz=" not in html


def test_state_walkthrough_pre_adhan_countdown(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    prayer = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    ).json()
    hour, minute = prayer["prayers"]["dhuhr"].split(":")
    target = datetime(2025, 10, 20, int(hour), int(minute)) - timedelta(minutes=2)
    _advance_to(surface, target)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "state-pre_adhan" in html
    assert 'id="countdown-label">Dhuhr call to prayer in<' in html
    assert f'data-target="2025-10-20T{hour}:{minute}' in html
    assert 'id="bounds"' in html  # same layout, bounds intact
    assert 'id="dim-skip"' not in html
    assert 'id="adhan-audio"' not in html


def test_state_walkthrough_adhan_countdown(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    prayer = client.get(
        "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
    ).json()
    hour, minute = prayer["prayers"]["dhuhr"].split(":")
    target = datetime(2025, 10, 20, int(hour), int(minute)) + timedelta(seconds=60)
    _advance_to(surface, target)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "state-adhan" in html
    assert "hidden data-now=" in html
    assert 'id="countdown-label">Iqomah in<' in html
    assert re.search(r'id="countdown" data-target="2025-10-20T\d{2}:\d{2}', html)
    assert 'id="row-dhuhr"' in html  # same layout, no overlay page
    assert 'id="dim-skip"' not in html


def test_state_walkthrough_iqamah_and_dim(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    event = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    ).json()
    iqamah = datetime.fromisoformat(event["iqamah_at"])
    _advance_to(surface, iqamah - timedelta(minutes=1))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="countdown-label">Iqomah in<' in html
    day = iqamah.date().isoformat()
    target_prefix = f'data-target="{day}T{iqamah.strftime("%H:%M")}'
    assert target_prefix in html
    _advance_to(surface, iqamah + timedelta(minutes=2))
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "state-salah_dim" in html
    assert "hidden data-now=" in html
    assert 'id="countdown"' not in html  # blank area: block omitted entirely
    assert 'id="countdown-label"' not in html
    assert 'id="dim-skip"' in html
    assert "data-dim-until=" in html
    assert 'id="row-fajr"' in html  # same layout dimmed, not a dim page


def test_state_walkthrough_jumuah_friday(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    prayer = client.get(
        "/api/prayer-day", params={"date": "2025-10-24", "zone": "SGR01"}
    ).json()
    hour, minute = prayer["prayers"]["dhuhr"].split(":")
    target = datetime(2025, 10, 24, int(hour), int(minute)) - timedelta(minutes=2)
    _advance_to(surface, target)
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "Jumuah" in html
    assert "Jumaat" not in html
    assert 'id="countdown-label">Jumuah call to prayer in<' in html
    assert 'prayer-row current" id="row-dhuhr"' in html


def test_display_per_card_iqamah_elements(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for key in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        pattern = rf'id="iqamah-{key}">\d{{1,2}}:\d{{2}}<small class="period">'
        pattern += r"(AM|PM)</small>"
        match = re.search(pattern, html)
        assert match, f"missing 12h iqamah element for {key}"


def test_display_new_structure_tokens(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "HALL-01"}).text
    for token in (
        "prayer-screen",
        "prayer-table",
        "prayer-row header",
        'id="live-clock"',
        "clock-h",
        "clock-m",
        "colon",
        "gregorian-date",
        "hijri-date",
        "mosque-name",
        "mosque-block",
        "additional-times",
        'id="hero-clock"',
    ):
        assert token in html
    # Countdown block renders only inside its window (walkthroughs pin it);
    # a fresh NORMAL render leaves the area blank.
    assert 'id="countdown-label"' not in html
    # Dropped render: boxes, bars, overlays, footer, trilingual extras.
    for token in (
        'id="overlay-adhan"',
        'id="iqamah-hero"',
        'id="note-pre"',
        'id="next-tomorrow"',
        'id="dim"',
        'id="dim-skip-hint"',
        'id="cards"',
        'id="ftr"',
        'id="carousel-dot"',
        'id="qr-hint"',
        "countdown-box",
        "data-bar-start",
    ):
        assert token not in html


def _seed_display_override(
    surface: SimpleNamespace, display_id: str, key: str, value: str
) -> None:
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            (display_id, "Main Hall", "Default"),
        )
        conn.execute(
            "INSERT INTO display_settings (display_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(display_id, key) DO UPDATE SET value = excluded.value",
            (display_id, key, value),
        )


def test_display_applies_per_display_theme_overrides(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "theme.palette", "midnight")
    _seed_display_override(surface, "HALL-01", "theme.countdown_style", "inline")
    _seed_display_override(surface, "HALL-01", "theme.clock_format", "12h")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert "palette-midnight" in html
    assert "palette-classic-green" not in html
    # Design-locked 12h: the knob never renders as a data attr; times carry periods.
    assert "data-clock-format" not in html
    assert '<small class="period">' in html


def test_display_unknown_id_falls_back_to_global_theme(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    html = client.get("/display", params={"id": "NOPE"}).text
    assert "palette-classic-green font-outfit density-comfortable" in html
    assert "data-clock-format" not in html
    assert "data-dim-source" not in html


def _dim_html_after_override(
    surface: SimpleNamespace, client: TestClient
) -> tuple[str, datetime]:
    """Render SALAH_DIM; return the html plus the iqamah instant for delta math."""
    event = client.get(
        "/api/next-event", params={"now": "2025-10-20T12:20:00+08:00"}
    ).json()
    iqamah = datetime.fromisoformat(event["iqamah_at"])
    _advance_to(surface, iqamah + timedelta(minutes=2))
    return client.get("/display", params={"id": "HALL-01"}).text, iqamah


def test_display_applies_per_display_dim_override(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "dim_minutes_override", "30")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="dim-skip"' not in html  # normal states carry no skip holder
    html, iqamah = _dim_html_after_override(surface, client)
    assert "state-salah_dim" in html
    dim_until = datetime.fromisoformat(
        re.search(r'data-dim-until="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    )
    assert (dim_until - iqamah).total_seconds() / 60 == 30


def test_display_hides_boundary_strip_on_override(
    surface: SimpleNamespace, client: TestClient
) -> None:
    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "theme.boundary_strip", "hide")
    html = client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="bounds"' not in html
    html = client.get("/display", params={"id": "OTHER"}).text
    assert 'id="bounds"' in html


def test_display_applies_group_dim_pin(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    _seed_settings(surface)
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
        conn.execute(
            "UPDATE display_groups SET dim_minutes_override = 30 WHERE name = ?",
            ("Default",),
        )
    html, iqamah = _dim_html_after_override(surface, client)
    dim_until = datetime.fromisoformat(
        re.search(r'data-dim-until="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    )
    assert (dim_until - iqamah).total_seconds() / 60 == 30


def test_display_display_dim_beats_group_dim(
    surface: SimpleNamespace, client: TestClient
) -> None:
    import re

    _seed_settings(surface)
    _seed_display_override(surface, "HALL-01", "dim_minutes_override", "25")
    with surface.db.write() as conn:
        conn.execute(
            "UPDATE display_groups SET dim_minutes_override = 30 WHERE name = ?",
            ("Default",),
        )
    html, iqamah = _dim_html_after_override(surface, client)
    dim_until = datetime.fromisoformat(
        re.search(r'data-dim-until="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    )
    assert (dim_until - iqamah).total_seconds() / 60 == 25


@pytest.mark.parametrize(
    "key,value",
    [
        ("theme.palette", "neon"),
        ("dim_minutes_override", "99"),
        ("dim_minutes_override", "abc"),
    ],
)
def test_display_corrupt_override_slates_503(
    surface: SimpleNamespace, client: TestClient, key: str, value: str
) -> None:
    _seed_settings(surface)
    with surface.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO displays (id, name, group_name) VALUES (?, ?, ?)",
            ("HALL-01", "Main Hall", "Default"),
        )
        conn.execute(
            "INSERT INTO display_settings (display_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(display_id, key) DO UPDATE SET value = excluded.value",
            ("HALL-01", key, value),
        )
    response = client.get("/display", params={"id": "HALL-01"})
    assert response.status_code == 503
    assert 'id="slate"' in response.text


@pytest.fixture
def audio_client(surface: SimpleNamespace, tmp_path) -> Any:
    """App sharing the surface repos/clock over an isolated upload dir."""
    from muhideen.adapters.playlist_repo import SqlitePlaylistRepo
    from muhideen.api.app import AppDeps, create_app

    deps = AppDeps(
        settings_repo=surface.settings_repo,
        prayer_repo=surface.prayer_repo,
        display_repo=surface.display_repo,
        user_repo=surface.user_repo,
        clock=surface.clock,
        event_bus=surface.bus,
        database=surface.db,
        playlist_repo=SqlitePlaylistRepo(surface.db),
        media_dir=tmp_path / "uploads",
    )
    app = create_app(deps)
    with TestClient(app) as test_client:
        yield test_client


def test_display_adhan_audio_matrix(
    surface: SimpleNamespace, audio_client: TestClient, tmp_path
) -> None:
    from muhideen.adapters.adhan_audio import store_adhan_audio

    blob = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 1024

    def _adhan_html() -> str:
        prayer = audio_client.get(
            "/api/prayer-day", params={"date": "2025-10-20", "zone": "SGR01"}
        ).json()
        hour, minute = prayer["prayers"]["dhuhr"].split(":")
        target = datetime(2025, 10, 20, int(hour), int(minute)) + timedelta(seconds=60)
        _advance_to(surface, target)
        return audio_client.get("/display", params={"id": "HALL-01"}).text

    # (a) fresh install (no file, defaults): silent in every state.
    _seed_settings(surface)
    html = audio_client.get("/display", params={"id": "HALL-01"}).text
    assert 'id="adhan-audio"' not in html
    html = _adhan_html()
    assert "state-adhan" in html
    assert 'id="row-dhuhr"' in html  # same layout, no overlay page
    assert 'id="adhan-audio"' not in html

    # (b) uploaded + enabled + non-quiet: ADHAN plays at set volume.
    store_adhan_audio(blob, tmp_path / "uploads")
    _seed_settings(surface, adhan_audio_enabled=True, adhan_volume=40)
    html = audio_client.get("/display", params={"id": "HALL-01"}).text
    assert "state-adhan" in html
    assert '<audio id="adhan-audio"' in html
    assert "/static/uploads/adhan.mp3" in html
    assert 'data-volume="40"' in html

    # (c) quiet hours covering now: silent.
    _seed_settings(
        surface,
        adhan_audio_enabled=True,
        adhan_volume=40,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
    )
    html = audio_client.get("/display", params={"id": "HALL-01"}).text
    assert "state-adhan" in html
    assert 'id="adhan-audio"' not in html

    # (d) muted next prayer: silent.
    _seed_settings(surface, adhan_audio_enabled=True, adhan_muted_prayers=["dhuhr"])
    html = audio_client.get("/display", params={"id": "HALL-01"}).text
    assert "state-adhan" in html
    assert 'id="adhan-audio"' not in html
