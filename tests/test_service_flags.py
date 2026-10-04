"""Service CLI takes --config (no --db); production app boots from files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "muhideen.example.json"


def _copy_example(tmp_path: Path) -> Path:
    """Copy the golden example to tmp (CWD-independent, never mutates repo)."""
    dest = tmp_path / "muhideen.json"
    shutil.copy(EXAMPLE, dest)
    return dest


def test_parser_takes_config_not_db() -> None:
    """The serve CLI takes --config/--prayer-buffer/--media-dir, not --db."""
    from muhideen.service import _parser

    args = _parser().parse_args(["--config", "config/muhideen.json"])
    assert args.config == "config/muhideen.json"
    assert not hasattr(args, "db")
    assert hasattr(args, "prayer_buffer")
    assert hasattr(args, "media_dir")


def test_parser_defaults_serve_file_config_on_loopback() -> None:
    """Zero-arg defaults: file config paths on 127.0.0.1:8000."""
    from muhideen.service import _parser

    args = _parser().parse_args([])
    assert args.config == "./config/muhideen.json"
    assert args.prayer_buffer == "./config/prayer_buffer.json"
    assert args.media_dir == "./media"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_main_wires_config_through_to_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() passes --config/--prayer-buffer/--media-dir through, workers=1."""
    from muhideen import service

    seen: dict[str, object] = {}

    def _app(config: str, **kwargs: object) -> object:
        seen["config"] = config
        seen["kwargs"] = kwargs
        return object()

    def _run(app: object, **kwargs: object) -> None:
        seen["run"] = kwargs

    monkeypatch.setattr(service, "create_production_app", _app)
    monkeypatch.setattr(service, "uvicorn", SimpleNamespace(run=_run))
    cfg = str(tmp_path / "muhideen.json")
    buf = str(tmp_path / "prayer_buffer.json")
    media = str(tmp_path / "media")
    service.main(["--config", cfg, "--prayer-buffer", buf, "--media-dir", media])
    assert seen["config"] == cfg
    assert seen["kwargs"] == {"prayer_buffer": buf, "media_dir": media}
    assert seen["run"] == {"host": "127.0.0.1", "port": 8000, "workers": 1}


def test_production_app_loads_example_config(tmp_path: Path) -> None:
    """create_production_app on a tmp copy of the example serves /api/version."""
    from muhideen.adapters.file_config import FileSettingsRepo
    from muhideen.api.app import create_production_app

    dest = _copy_example(tmp_path)
    assert FileSettingsRepo(dest).load().zone == "SGR01"
    app = create_production_app(dest, run_background=False)
    with TestClient(app) as client:
        assert client.get("/api/version").status_code == 200


def test_production_app_missing_config_fails_fast(tmp_path: Path) -> None:
    """A missing config file raises instead of booting (replaces seed flow)."""
    from muhideen.api.app import create_production_app

    # Pin the exact contract: missing file is FileNotFoundError, not a 503.
    with pytest.raises(FileNotFoundError):
        create_production_app(tmp_path / "absent.json", run_background=False)


def test_production_app_prayer_buffer_defaults_next_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The buffer defaults to a sibling of the config file (CWD-independent)."""
    import muhideen.api.app as app_module
    from muhideen.api.app import create_production_app

    dest = _copy_example(tmp_path)
    captured: dict[str, object] = {}
    real_repo = app_module.FilePrayerRepo

    def _spy(buffer_path: str | Path, manual_days: object = ()) -> object:
        captured["buffer"] = str(buffer_path)
        return real_repo(buffer_path, manual_days)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "FilePrayerRepo", _spy)
    create_production_app(dest, run_background=False)
    assert captured["buffer"] == str(tmp_path / "prayer_buffer.json")


def test_example_config_zone_survives_json_round_trip(tmp_path: Path) -> None:
    """Tmp copy keeps the golden zone after a settings save round-trip."""
    from muhideen.adapters.file_config import FileSettingsRepo

    dest = _copy_example(tmp_path)
    repo = FileSettingsRepo(dest)
    settings = repo.load()
    repo.save(settings)
    raw = json.loads(dest.read_text())
    assert raw["masjid"]["zone"] == "SGR01"
