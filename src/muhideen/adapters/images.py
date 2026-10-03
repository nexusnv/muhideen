"""Image ingestion: allowlisted re-encode with metadata stripped.

Uploads are re-encoded from decoded pixels (never a byte copy), so EXIF
and other carried metadata cannot survive. Oversize input, undecodable
bytes, and non-allowlisted formats raise ``ValueError``.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL.Image import DecompressionBombError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
"""Upload cap: inputs larger than 5MB are rejected before decoding."""

MAX_IMAGE_PIXELS = 25_000_000
"""Decoded pixel cap (~25MP): larger images are rejected before allocation."""

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

MAX_DIMENSION = 1920
"""Long-edge cap: larger images are downscaled preserving aspect ratio."""

_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
"""Decoded formats accepted, each with its on-disk suffix."""


def _to_display_size(image: Image.Image) -> Image.Image:
    """Downscale images whose long edge exceeds the cap, in place."""
    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    return image


def _for_jpeg(image: Image.Image) -> Image.Image:
    """Drop alpha for JPEG output, compositing translucent pixels on white."""
    if image.mode in ("RGBA", "LA"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGB"), mask=image.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def store_image(data: bytes, dest_dir: str | Path, *, name: str | None = None) -> Path:
    """Re-encode ``data`` into ``dest_dir`` without metadata; return its path.

    Raises ``ValueError`` when the input exceeds 5MB, cannot be decoded,
    or decodes to a format outside JPG/PNG/WebP.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds 5MB limit: {len(data)} bytes")
    try:
        with Image.open(BytesIO(data)) as raw:
            raw.load()
            image_format = raw.format
            image = raw.copy()
    except (OSError, ValueError, DecompressionBombError) as exc:
        raise ValueError(f"undecodable image bytes: {exc}") from exc
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"image exceeds pixel limit {MAX_IMAGE_PIXELS}: "
            f"{image.width}x{image.height}"
        )
    if not isinstance(image_format, str) or image_format not in _FORMAT_SUFFIXES:
        raise ValueError(f"unsupported image format: {image_format}")
    if image_format == "JPEG":
        image = _for_jpeg(image)
    image = _to_display_size(image)
    # A fresh copy with cleared info guarantees no carried metadata block
    # (Pillow re-embeds ``info["exif"]`` on save when it is present).
    clean = image.copy()
    clean.info.clear()
    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = name if name is not None else uuid.uuid4().hex
    if name is not None and ("/" in stem or "\\" in stem or ".." in stem):
        raise ValueError(f"unsafe image name: {name!r}")
    path = directory / f"{stem}{_FORMAT_SUFFIXES[image_format]}"
    if image_format == "JPEG":
        clean.save(path, format=image_format, quality=85)
    else:
        clean.save(path, format=image_format)
    return path
