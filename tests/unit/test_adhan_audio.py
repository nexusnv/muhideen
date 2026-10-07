"""Adhan file: configured media-relative path, contained resolution, stable URL."""

import pytest

pytestmark = pytest.mark.unit


def test_adhan_audio_url_helper_default_and_custom(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import ADHAN_FILENAME
    from muhideen.api.app import _STATIC_DIR, _adhan_audio_url

    assert _adhan_audio_url(_STATIC_DIR / "uploads") == "/static/uploads/adhan.mp3"
    custom = _STATIC_DIR / "uploads" / "custom-subdir"
    assert _adhan_audio_url(custom) == f"/static/uploads/custom-subdir/{ADHAN_FILENAME}"
    assert _adhan_audio_url(tmp_path / "elsewhere") == f"/media/{ADHAN_FILENAME}"
    assert _adhan_audio_url(tmp_path / "elsewhere", "custom/x.mp3") == (
        "/media/custom/x.mp3"
    )


def test_adhan_audio_url_normalizes_dotdot_segments(tmp_path) -> None:
    from muhideen.api.app import _adhan_audio_url

    assert _adhan_audio_url(tmp_path / "elsewhere", "sub/../x.mp3") == "/media/x.mp3"


def test_resolve_rejects_absolute_and_escape(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ConfigError):
        resolve_adhan_path("/etc/passwd", media)
    with pytest.raises(ConfigError):
        resolve_adhan_path("../secret.mp3", media)
    with pytest.raises(ConfigError):
        resolve_adhan_path("sub/../../escape.mp3", media)


@pytest.mark.parametrize("degenerate", ["media", "media/", "media//adhan.mp3"])
def test_resolve_rejects_bare_media_prefix(tmp_path, degenerate: str) -> None:
    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ConfigError):
        resolve_adhan_path(degenerate, media)


@pytest.mark.parametrize("bad", ["x?y.mp3", "weird#1.mp3", "a/b?c", "a#b/c.mp3"])
def test_resolve_rejects_url_structural_chars(tmp_path, bad: str) -> None:
    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ConfigError, match=r"\?|#"):
        resolve_adhan_path(bad, media)


def test_normalize_rejected_consistently_at_every_layer(tmp_path) -> None:
    """Enabled-audio regression: load validators and resolve must agree."""
    import copy

    from pydantic import ValidationError

    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.adapters.file_models import ConfigFile
    from muhideen.api.app import _adhan_audio_url
    from muhideen.core.errors import ConfigError
    from muhideen.core.values import Settings

    media = tmp_path / "media"
    media.mkdir()
    for bad in ("media//adhan.mp3", "x?y.mp3", "a#b.mp3", "a\x00.mp3"):
        cfg = {
            "$schemaVersion": 1,
            "masjid": {"name": "M", "timezone": "Asia/Kuala_Lumpur"},
            "schedule": {"sync_provider": "none"},
            "adhan_audio": {"enabled": True, "file": bad},
        }
        with pytest.raises(ValidationError):
            ConfigFile.model_validate(copy.deepcopy(cfg))
        with pytest.raises(ValueError):
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                adhan_audio_enabled=True,
                adhan_audio_file=bad,
            )
        with pytest.raises(ConfigError):
            resolve_adhan_path(bad, media)
        with pytest.raises(ConfigError):
            _adhan_audio_url(media, bad)
    # Disabled audio ignores the file at load but resolve still rejects
    # (display only resolves when enabled).
    for bad in ("media//adhan.mp3", "../escape.mp3", "a\x00.mp3"):
        cfg = {
            "$schemaVersion": 1,
            "masjid": {"name": "M", "timezone": "Asia/Kuala_Lumpur"},
            "schedule": {"sync_provider": "none"},
            "adhan_audio": {"enabled": False, "file": bad},
        }
        assert ConfigFile.model_validate(copy.deepcopy(cfg)).adhan_audio.file == bad
        assert (
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                adhan_audio_enabled=False,
                adhan_audio_file=bad,
            ).adhan_audio_file
            == bad
        )
        with pytest.raises(ConfigError):
            resolve_adhan_path(bad, media)
        with pytest.raises(ConfigError):
            _adhan_audio_url(media, bad)


def test_resolve_strips_legacy_media_prefix_and_urls_are_stable(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.api.app import _adhan_audio_url

    media = tmp_path / "media"
    media.mkdir()
    assert resolve_adhan_path("media/adhan.mp3", media) == media / "adhan.mp3"
    assert resolve_adhan_path("adhan.mp3", media) == media / "adhan.mp3"
    assert resolve_adhan_path("custom/x.mp3", media) == media / "custom" / "x.mp3"
    assert _adhan_audio_url(media, "media/adhan.mp3") == "/media/adhan.mp3"
    assert _adhan_audio_url(media, "media/adhan.mp3") == _adhan_audio_url(
        media, "adhan.mp3"
    )


@pytest.mark.parametrize("bad", [" ", "   ", "custom/dir/", "media/"])
def test_resolve_rejects_whitespace_only_and_directories(tmp_path, bad: str) -> None:
    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.api.app import _adhan_audio_url
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ConfigError):
        resolve_adhan_path(bad, media)
    with pytest.raises(ConfigError):
        _adhan_audio_url(media, bad)


def test_adhan_audio_url_percent_encodes_special_chars(tmp_path) -> None:
    from muhideen.api.app import _adhan_audio_url

    media = tmp_path / "media"
    media.mkdir()
    # Uvicorn/Starlette decode % escapes before StaticFiles resolves, so a
    # literal % in the filename must be encoded (and / must be preserved).
    assert _adhan_audio_url(media, "mix%20.mp3") == "/media/mix%2520.mp3"
    assert _adhan_audio_url(media, "my adhan.mp3") == "/media/my%20adhan.mp3"
    assert _adhan_audio_url(media, "sub/dir.mp3") == "/media/sub/dir.mp3"
    assert _adhan_audio_url(media, "adhan.mp3") == "/media/adhan.mp3"


def test_resolve_translates_symlink_loop_to_config_error(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from muhideen.adapters.adhan_audio import resolve_adhan_path
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    # Path.resolve() raises RuntimeError on symlink loops (py3.11/3.12);
    # simulate it so the display keeps serving the 503 slate, not a 500.
    real_resolve = Path.resolve

    def looping_resolve(self, *args, **kwargs):
        if str(self).startswith(str(media)):
            raise RuntimeError(f"Symlink loop from {self!r}")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", looping_resolve)
    with pytest.raises(ConfigError, match="Symlink loop"):
        resolve_adhan_path("adhan.mp3", media)


def test_adhan_audio_url_rejects_symlink_escape(tmp_path) -> None:
    import os

    from muhideen.api.app import _adhan_audio_url
    from muhideen.core.errors import ConfigError

    media = tmp_path / "media"
    media.mkdir()
    os.symlink("/etc", media / "link")
    with pytest.raises(ConfigError, match="escapes"):
        _adhan_audio_url(media, "link/passwd")


def test_quiet_hours_start_eq_end_never_quiet() -> None:  # Issue #58
    from muhideen.api.app import _in_quiet_hours

    assert _in_quiet_hours("12:00", "12:00", "12:00") is False
    assert _in_quiet_hours("00:00", "00:00", "00:00") is False
    # Sanity rows: established ranges keep behaving.
    assert _in_quiet_hours("23:00", "22:00", "06:00") is True
    assert _in_quiet_hours("07:00", "22:00", "06:00") is False
    assert _in_quiet_hours("12:00", "09:00", "17:00") is True
    assert _in_quiet_hours("08:59", "09:00", "17:00") is False
    assert _in_quiet_hours("12:00", None, None) is False


def test_adhan_playback_allows_none_next_prayer() -> None:  # Issue #60
    from muhideen.api.app import _adhan_playback_allowed

    assert (
        _adhan_playback_allowed(
            enabled=True,
            next_prayer=None,
            muted_prayers=["fajr"],
            now_hhmm="12:00",
            quiet_start=None,
            quiet_end=None,
        )
        is True
    )


def test_adhan_playback_respects_muted_quiet_and_enabled() -> None:  # Issue #60
    from muhideen.api.app import _adhan_playback_allowed

    allowed = dict(
        enabled=True,
        next_prayer="fajr",
        muted_prayers=[],
        now_hhmm="12:00",
        quiet_start=None,
        quiet_end=None,
    )
    assert _adhan_playback_allowed(**allowed) is True
    assert _adhan_playback_allowed(**{**allowed, "muted_prayers": ["fajr"]}) is False
    assert (
        _adhan_playback_allowed(
            **{**allowed, "quiet_start": "09:00", "quiet_end": "17:00"}
        )
        is False
    )
    assert _adhan_playback_allowed(**{**allowed, "enabled": False}) is False
