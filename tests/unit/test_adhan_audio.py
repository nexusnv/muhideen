"""Adhan audio store: MP3-only, 10MB cap, canonical name."""

import pytest

pytestmark = pytest.mark.unit

VALID_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 1024  # minimal ID3 header
FRAME_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 1024  # MPEG frame sync


def test_store_accepts_id3_and_frame_sync(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import store_adhan_audio

    for blob in (VALID_MP3, FRAME_MP3):
        path = store_adhan_audio(blob, tmp_path)
        assert path.name == "adhan.mp3" and path.read_bytes() == blob


def test_store_rejects_non_mp3_and_oversize(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import MAX_ADHAN_BYTES, store_adhan_audio

    with pytest.raises(ValueError, match="unsupported audio"):
        store_adhan_audio(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, tmp_path)
    with pytest.raises(ValueError, match="exceeds"):
        store_adhan_audio(b"ID3" + b"\x00" * (MAX_ADHAN_BYTES + 1), tmp_path)


def test_delete_is_missing_ok(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import delete_adhan_audio, store_adhan_audio

    store_adhan_audio(VALID_MP3, tmp_path)
    assert delete_adhan_audio(tmp_path) is True
    assert delete_adhan_audio(tmp_path) is False


def test_store_is_atomic_and_leaves_no_tmp_litter(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import store_adhan_audio

    store_adhan_audio(VALID_MP3, tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    store_adhan_audio(FRAME_MP3, tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "adhan.mp3").read_bytes() == FRAME_MP3


def test_adhan_audio_url_helper_default_and_custom(tmp_path) -> None:
    from muhideen.adapters.adhan_audio import ADHAN_FILENAME
    from muhideen.api.app import _STATIC_DIR, _adhan_audio_url

    assert _adhan_audio_url(_STATIC_DIR / "uploads") == "/static/uploads/adhan.mp3"
    custom = _STATIC_DIR / "uploads" / "custom-subdir"
    assert _adhan_audio_url(custom) == f"/static/uploads/custom-subdir/{ADHAN_FILENAME}"
    # Outside the static root (both deploy targets) the file is served
    # from the /media mount, never the dead /static/uploads fallback.
    assert _adhan_audio_url(tmp_path / "elsewhere") == f"/media/{ADHAN_FILENAME}"
    assert _adhan_audio_url(tmp_path / "elsewhere", "custom/x.mp3") == (
        "/media/custom/x.mp3"
    )


def test_sniff_rejects_short_blobs() -> None:
    from muhideen.adapters.adhan_audio import _is_mp3

    assert _is_mp3(b"ID") is False
    assert _is_mp3(b"ID3") is False


def test_store_failure_leaves_no_tmp_litter(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write removes its tmp file before re-raising."""
    import os

    from muhideen.adapters.adhan_audio import store_adhan_audio

    def _boom(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        store_adhan_audio(VALID_MP3, tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []


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


def test_resolve_strips_legacy_media_prefix_and_urls_are_stable(
    tmp_path,
) -> None:
    from muhideen.adapters.adhan_audio import adhan_url_for, resolve_adhan_path

    media = tmp_path / "media"
    media.mkdir()
    assert resolve_adhan_path("media/adhan.mp3", media) == media / "adhan.mp3"
    assert resolve_adhan_path("adhan.mp3", media) == media / "adhan.mp3"
    assert resolve_adhan_path("custom/x.mp3", media) == media / "custom" / "x.mp3"
    assert adhan_url_for("media/adhan.mp3", media) == "/media/adhan.mp3"
    assert adhan_url_for("media/adhan.mp3", media) == adhan_url_for("adhan.mp3", media)
