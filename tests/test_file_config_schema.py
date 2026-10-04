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
