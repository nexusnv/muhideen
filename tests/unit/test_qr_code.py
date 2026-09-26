"""QR data-URI guards (slice 1B-3)."""

import base64

import pytest

pytestmark = pytest.mark.unit

TEXT = "http://muhideen.local:8000/admin"


def test_data_uri_shape_and_png_magic() -> None:
    from muhideen.adapters.qr_code import qr_data_uri

    uri = qr_data_uri(TEXT)
    assert uri.startswith("data:image/png;base64,")
    raw = base64.b64decode(uri.removeprefix("data:image/png;base64,"))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(raw) > 200


def test_deterministic_for_same_input() -> None:
    from muhideen.adapters.qr_code import qr_data_uri

    assert qr_data_uri(TEXT) == qr_data_uri(TEXT)
