"""Schema tests for config/muhideen.json file models (task 1)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from muhideen.adapters.file_models import ConfigFile, Display

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"


def _example_raw() -> dict:
    return json.loads(EXAMPLE.read_text())


def test_example_config_validates():
    raw = _example_raw()
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.effective_zone == "SGR01"
    assert cfg.schedule.jakim.zone == "SGR01"
    assert cfg.displays["main-hall"].language == "en"
    assert cfg.displays["entrance"].theme.palette == "midnight"


def test_top_level_rejects_unknown_key():
    raw = _example_raw()
    raw["bogus_key"] = 1
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_display_rejects_schedule_keys():
    with pytest.raises(ValidationError):
        Display.model_validate(
            {
                "name": "X",
                "language": "en",
                "theme": {},
                "schedule": {},
                "iqamah_rules": [],
            }
        )


def test_iqamah_rules_must_be_exactly_six():
    raw = _example_raw()
    raw["timing"]["iqamah_rules"] = raw["timing"]["iqamah_rules"][:5]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)
    raw2 = _example_raw()
    raw2["timing"]["iqamah_rules"] = raw2["timing"]["iqamah_rules"] + [
        {
            "prayer": "fajr",
            "mode": "delay",
            "delay_minutes": 10,
            "fixed_time": None,
        }
    ]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw2)


def test_iqamah_rules_reject_duplicates_and_missing():
    raw = _example_raw()
    fajr = raw["timing"]["iqamah_rules"][0]
    raw["timing"]["iqamah_rules"] = [dict(fajr) for _ in range(6)]
    with pytest.raises(ValidationError, match="duplicate"):
        ConfigFile.model_validate(raw)


def test_playlist_cycle_pairing_enforced():
    raw = _example_raw()
    raw["playlists"][0]["cycle_mode"] = "repeat"
    raw["playlists"][0]["max_cycles"] = None
    with pytest.raises(ValidationError, match="max_cycles"):
        ConfigFile.model_validate(raw)


def test_display_language_accepts_bm_alias():
    raw = _example_raw()
    raw["displays"]["main-hall"]["language"] = "bm"
    cfg = ConfigFile.model_validate(raw)
    assert cfg.displays["main-hall"].language == "bm"


def test_schema_version_rejects_unknown():
    raw = _example_raw()
    raw["$schemaVersion"] = 999
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_schema_version_is_required():
    """Unversioned files fail closed instead of assuming version 1."""
    raw = _example_raw()
    del raw["$schemaVersion"]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_schema_version_rejects_snake_case_spelling():
    """Only the ``$schemaVersion`` alias validates; ``schema_version`` does not."""
    raw = _example_raw()
    del raw["$schemaVersion"]
    raw["schema_version"] = 1
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_duplicate_manual_day_dates_rejected():
    """Two pins for one date would serve divergently (get_day vs last_known)."""
    raw = _example_raw()
    raw["schedule"]["manual_days"].append(dict(raw["schedule"]["manual_days"][0]))
    with pytest.raises(ValidationError, match="duplicate manual_day"):
        ConfigFile.model_validate(raw)


def test_dateless_markers_pin_rejected():
    """A pin with no markers corrects nothing — rejected at load."""
    raw = _example_raw()
    raw["schedule"]["manual_days"] = [{"date": "2026-04-01"}]
    with pytest.raises(ValidationError, match="at least one marker"):
        ConfigFile.model_validate(raw)


def test_partial_pin_validates():
    """A pin with a subset of markers is a valid partial correction."""
    raw = _example_raw()
    raw["schedule"]["manual_days"] = [{"date": "2026-04-01", "maghrib": "19:15"}]
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.manual_days[0].maghrib == "19:15"
    assert cfg.schedule.manual_days[0].fajr is None


def test_displays_may_be_empty_or_missing():
    raw = _example_raw()
    raw["displays"] = {}
    assert ConfigFile.model_validate(raw).displays == {}
    minimal = {
        "$schemaVersion": 1,
        "masjid": raw["masjid"],
        "schedule": {
            "sync_provider": "jakim",
            "jakim": {"zone": "SGR01"},
        },
    }
    assert ConfigFile.model_validate(minimal).displays == {}


def test_masjid_has_no_zone_code():
    """Zone codes are provider arguments, not profile identity."""
    raw = _example_raw()
    assert set(raw["masjid"]) == {"name", "timezone"}
    raw["masjid"]["zone"] = "SGR01"
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_sync_provider_is_required_with_no_default():
    """An international app pins no country's API as the default."""
    raw = _example_raw()
    del raw["schedule"]["sync_provider"]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_sync_provider_rejects_unknown():
    raw = _example_raw()
    raw["schedule"]["sync_provider"] = "muslimsalat"
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_none_provider_needs_no_zone_or_coordinates():
    """Explicit offline validates with neither fetch key nor coords."""
    raw = _example_raw()
    raw["schedule"]["sync_provider"] = "none"
    del raw["schedule"]["jakim"]
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.effective_zone == "local"
    raw["schedule"]["lat"] = 3.139
    raw["schedule"]["lon"] = 101.6869
    assert ConfigFile.model_validate(raw).schedule.sync_provider == "none"


def test_jakim_provider_requires_its_zone():
    raw = _example_raw()
    raw["schedule"]["jakim"] = {}
    with pytest.raises(ValidationError, match="jakim.zone"):
        ConfigFile.model_validate(raw)


def test_effective_zone_prefers_label_then_fetch_key():
    raw = _example_raw()
    assert ConfigFile.model_validate(raw).schedule.effective_zone == "SGR01"
    raw["schedule"]["zone"] = "surau-alhuda"
    assert ConfigFile.model_validate(raw).schedule.effective_zone == "surau-alhuda"
    raw["schedule"]["sync_provider"] = "aladhan"
    raw["schedule"]["lat"] = 3.139
    raw["schedule"]["lon"] = 101.6869
    del raw["schedule"]["zone"]
    del raw["schedule"]["jakim"]
    assert ConfigFile.model_validate(raw).schedule.effective_zone == "local"


def test_aladhan_base_url_must_be_http():
    for bad in ("ftp://example.com/v1", "not-a-url", "https://"):
        raw = _example_raw()
        raw["schedule"]["aladhan"]["base_url"] = bad
        with pytest.raises(ValidationError):
            ConfigFile.model_validate(raw)


def test_aladhan_base_url_trailing_slash_stripped():
    raw = _example_raw()
    raw["schedule"]["aladhan"]["base_url"] = "https://aladhan.api.islamic.network/v1/"
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.aladhan.base_url == "https://aladhan.api.islamic.network/v1"


def test_aladhan_method_bounded():
    for bad in (-1, 24):
        raw = _example_raw()
        raw["schedule"]["aladhan"]["method"] = bad
        with pytest.raises(ValidationError):
            ConfigFile.model_validate(raw)


def test_aladhan_provider_requires_coordinates():
    raw = _example_raw()
    raw["schedule"]["sync_provider"] = "aladhan"
    assert raw["schedule"]["lat"] is None
    with pytest.raises(ValidationError, match="lat and lon"):
        ConfigFile.model_validate(raw)
    raw["schedule"]["lat"] = 3.139
    raw["schedule"]["lon"] = 101.6869
    assert ConfigFile.model_validate(raw).schedule.sync_provider == "aladhan"


def test_schedule_section_is_required():
    """A config without a schedule section fails closed (no silent offline)."""
    raw = _example_raw()
    del raw["schedule"]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_lat_lon_must_be_paired():
    raw = _example_raw()
    raw["schedule"]["lat"] = 3.139
    assert raw["schedule"]["lon"] is None
    with pytest.raises(ValidationError, match="lat and lon"):
        ConfigFile.model_validate(raw)


def test_fixed_iqamah_rule_needs_time():
    raw = _example_raw()
    raw["timing"]["iqamah_rules"][0]["mode"] = "fixed"
    raw["timing"]["iqamah_rules"][0]["fixed_time"] = None
    with pytest.raises(ValidationError, match="without time"):
        ConfigFile.model_validate(raw)


def test_iqamah_rules_must_cover_every_prayer():
    """Six unique slots that skip a prayer fail loudly (bypasses literals)."""
    from types import SimpleNamespace

    from muhideen.adapters.file_models import Timing

    prayers = ("fajr", "dhuhr", "asr", "maghrib", "isha", "bogus")
    timing = Timing.model_construct(
        iqamah_rules=[SimpleNamespace(prayer=p) for p in prayers]
    )
    with pytest.raises(ValueError, match="missing iqamah rule"):
        Timing._rules_cover_prayers_exactly(timing)


def test_quiet_hours_must_be_paired():
    raw = _example_raw()
    raw["adhan_audio"]["quiet_hours_start"] = "22:00"
    assert raw["adhan_audio"]["quiet_hours_end"] is None
    with pytest.raises(ValidationError, match="quiet hours"):
        ConfigFile.model_validate(raw)


def test_display_theme_getitem():
    cfg = ConfigFile.model_validate(_example_raw())
    theme = cfg.displays["main-hall"].theme
    assert theme["palette"] == theme.palette
    with pytest.raises(KeyError):
        theme["bogus_knob"]


def test_custom_colors_accept_none_and_valid_map():
    raw = _example_raw()
    raw["displays"]["main-hall"]["custom_colors"] = None
    assert ConfigFile.model_validate(raw).displays["main-hall"].custom_colors is None
    raw["displays"]["main-hall"]["custom_colors"] = {"background": "#112233"}
    validated = ConfigFile.model_validate(raw).displays["main-hall"].custom_colors
    assert validated == {"background": "#112233"}


def test_custom_colors_reject_unknown_key_and_bad_hex():
    raw = _example_raw()
    raw["displays"]["main-hall"]["custom_colors"] = {"wallpaper": "#112233"}
    with pytest.raises(ValidationError, match="unknown custom color"):
        ConfigFile.model_validate(raw)
    raw["displays"]["main-hall"]["custom_colors"] = {"background": "112233"}
    with pytest.raises(ValidationError, match="#rrggbb"):
        ConfigFile.model_validate(raw)


def test_playlist_repeat_needs_cycles_and_indefinite_forbids_them():
    raw = _example_raw()
    raw["playlists"][0]["cycle_mode"] = "repeat"
    raw["playlists"][0]["max_cycles"] = None
    with pytest.raises(ValidationError, match="max_cycles"):
        ConfigFile.model_validate(raw)
    raw["playlists"][0]["cycle_mode"] = "indefinite"
    raw["playlists"][0]["max_cycles"] = 3
    with pytest.raises(ValidationError, match="max_cycles"):
        ConfigFile.model_validate(raw)


def test_playlist_items_capped_at_fifty():
    from muhideen.adapters.file_models import PlaylistFile

    raw = _example_raw()
    raw["playlists"][0]["items"] = [
        {"image_path": f"media/{i}.jpg", "duration_s": 5, "sort_order": i}
        for i in range(51)
    ]
    with pytest.raises(ValidationError, match="capped at 50"):
        ConfigFile.model_validate(raw)
    valid = _example_raw()
    assert PlaylistFile.model_validate(valid["playlists"][0]).id == "announcements"


def test_adhan_file_rejects_absolute_and_traversal() -> None:
    import copy

    base = {
        "$schemaVersion": 1,
        "masjid": {"name": "M", "timezone": "Asia/Kuala_Lumpur"},
        "schedule": {"sync_provider": "none"},
    }
    bads = ("/etc/passwd", "/media/adhan.mp3", "../secret.mp3", "a/../../b.mp3")
    for bad in bads:
        cfg = copy.deepcopy(base)
        cfg["adhan_audio"] = {"enabled": True, "file": bad}
        with pytest.raises(ValidationError):
            ConfigFile.model_validate(cfg)
        # Disabled audio ignores the file setting so a stale path cannot
        # brick the display; resolve still rejects (only called when enabled).
        allowed = copy.deepcopy(base)
        allowed["adhan_audio"] = {"enabled": False, "file": bad}
        assert ConfigFile.model_validate(allowed).adhan_audio.file == bad
