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
