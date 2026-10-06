"""Image ingestion: allowlist, EXIF strip, size cap, downscale (slice 1C, Task 7)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.unit


def _jpeg_bytes(
    width: int = 64, height: int = 64, *, exif: bytes | None = None
) -> bytes:
    img = Image.new("RGB", (width, height), (200, 30, 30))
    buf = BytesIO()
    if exif is not None:
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _exif_bytes() -> bytes:
    exif = Image.Exif()
    exif[274] = 6  # Orientation
    exif[271] = "TestMake"
    return exif.tobytes()


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    img = Image.new("RGBA", (width, height), (30, 120, 200, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _webp_bytes(width: int = 64, height: int = 64) -> bytes:
    img = Image.new("RGB", (width, height), (30, 200, 120))
    buf = BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _bmp_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), (0, 128, 0))
    buf = BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _gif_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), (0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def test_store_strips_exif_proof(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    raw = _jpeg_bytes(exif=_exif_bytes())
    assert Image.open(BytesIO(raw)).getexif() != {}
    out = store_image(raw, tmp_path)
    assert Image.open(out).getexif() == {}


def test_store_png_carries_no_exif(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    out = store_image(_png_bytes(), tmp_path)
    assert Image.open(out).getexif() == {}


def test_store_rejects_over_5mb(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    with pytest.raises(ValueError, match="5MB"):
        store_image(b"\x00" * (5 * 1024 * 1024 + 1), tmp_path)


def test_store_rejects_bmp(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    with pytest.raises(ValueError, match="format"):
        store_image(_bmp_bytes(), tmp_path)


def test_store_rejects_gif(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    with pytest.raises(ValueError, match="format"):
        store_image(_gif_bytes(), tmp_path)


def test_store_rejects_garbage_and_empty(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    with pytest.raises(ValueError):
        store_image(b"not an image at all", tmp_path)
    with pytest.raises(ValueError):
        store_image(b"", tmp_path)


def test_store_downscales_to_1920(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    out = store_image(_jpeg_bytes(3000, 2000), tmp_path)
    with Image.open(out) as img:
        assert max(img.size) <= 1920
        assert img.size == (1920, 1280)


def test_store_downscales_portrait(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    out = store_image(_jpeg_bytes(1000, 3000), tmp_path)
    with Image.open(out) as img:
        assert max(img.size) <= 1920
        assert img.size == (640, 1920)


def test_store_keeps_small_image_dimensions(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    out = store_image(_jpeg_bytes(800, 600), tmp_path)
    with Image.open(out) as img:
        assert img.size == (800, 600)


def test_store_round_trips_png_and_webp(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    png_out = store_image(_png_bytes(), tmp_path)
    assert png_out.suffix == ".png"
    with Image.open(png_out) as img:
        assert img.format == "PNG"

    webp_out = store_image(_webp_bytes(), tmp_path)
    assert webp_out.suffix == ".webp"
    with Image.open(webp_out) as img:
        assert img.format == "WEBP"


def test_store_returns_file_inside_dest_dir(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    dest = tmp_path / "uploads"
    out = store_image(_jpeg_bytes(), dest)
    assert out.parent == dest
    assert out.suffix == ".jpg"
    assert out.is_file()
    with Image.open(out) as img:
        assert img.format == "JPEG"


def test_store_rejects_unsafe_name(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    with pytest.raises(ValueError, match="unsafe image name"):
        store_image(_jpeg_bytes(), tmp_path, name="../escape")
    with pytest.raises(ValueError, match="unsafe image name"):
        store_image(_jpeg_bytes(), tmp_path, name="sub/dir")


def test_store_distinct_names_do_not_collide(tmp_path: Path) -> None:
    from muhideen.adapters.images import store_image

    raw = _jpeg_bytes()
    first = store_image(raw, tmp_path)
    second = store_image(raw, tmp_path)
    assert first != second
    assert first.is_file() and second.is_file()


def test_for_jpeg_flattens_alpha_and_converts_modes() -> None:
    from muhideen.adapters.images import _for_jpeg

    flat = _for_jpeg(Image.new("RGBA", (8, 8), (10, 20, 30, 128)))
    assert flat.mode == "RGB"
    assert _for_jpeg(Image.new("L", (8, 8), 128)).mode == "RGB"
    rgb = Image.new("RGB", (8, 8), (1, 2, 3))
    assert _for_jpeg(rgb) is rgb


def test_store_rejects_oversize_pixel_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muhideen.adapters.images as images_module
    from muhideen.adapters.images import store_image

    monkeypatch.setattr(images_module, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="pixel limit"):
        store_image(_jpeg_bytes(64, 64), tmp_path)
