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
