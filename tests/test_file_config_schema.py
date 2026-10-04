"""Schema tests for config/muhideen.json file models (task 1)."""

from pathlib import Path

import json
import pytest
from pydantic import ValidationError

from muhideen.adapters.file_models import ConfigFile, Display


def test_example_config_validates():
    raw = json.loads(Path("config/muhideen.example.json").read_text())
    cfg = ConfigFile.model_validate(raw)
    assert cfg.masjid.zone == "SGR01"
    assert cfg.displays["main-hall"].language == "en"
    assert cfg.displays["entrance"].theme["palette"] == "midnight"


def test_top_level_rejects_unknown_key():
    raw = json.loads(Path("config/muhideen.example.json").read_text())
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
    raw = json.loads(Path("config/muhideen.example.json").read_text())
    raw["timing"]["iqamah_rules"] = raw["timing"]["iqamah_rules"][:5]
    with pytest.raises(ValidationError):
        ConfigFile.model_validate(raw)
    raw2 = json.loads(Path("config/muhideen.example.json").read_text())
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
