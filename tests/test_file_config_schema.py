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
    assert cfg.masjid.zone == "SGR01"
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


def test_displays_may_be_empty_or_missing():
    raw = _example_raw()
    raw["displays"] = {}
    assert ConfigFile.model_validate(raw).displays == {}
    minimal = {"$schemaVersion": 1, "masjid": raw["masjid"]}
    assert ConfigFile.model_validate(minimal).displays == {}


def test_sync_provider_defaults_jakim_with_aladhan_knobs():
    raw = _example_raw()
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.sync_provider == "jakim"
    assert cfg.schedule.aladhan_base_url == "https://api.aladhan.com/v1"
    assert cfg.schedule.aladhan_method == 17


def test_sync_provider_rejects_unknown():
    raw = _example_raw()
    raw["schedule"]["sync_provider"] = "muslimsalat"
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)


def test_aladhan_base_url_must_be_http():
    for bad in ("ftp://example.com/v1", "not-a-url", "https://"):
        raw = _example_raw()
        raw["schedule"]["aladhan_base_url"] = bad
        with pytest.raises(ValidationError):
            ConfigFile.model_validate(raw)


def test_aladhan_base_url_trailing_slash_stripped():
    raw = _example_raw()
    raw["schedule"]["aladhan_base_url"] = "https://aladhan.api.islamic.network/v1/"
    cfg = ConfigFile.model_validate(raw)
    assert cfg.schedule.aladhan_base_url == "https://aladhan.api.islamic.network/v1"


def test_aladhan_method_bounded():
    for bad in (-1, 24):
        raw = _example_raw()
        raw["schedule"]["aladhan_method"] = bad
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
