"""Server-side QR PNG as a data URI (FR-6.3 fast-connect).

Pure function: text in, ``data:image/png;base64,...`` out. Callers pass
the LAN URL only — never credentials. segno is pinned for zero runtime
dependencies; the matrix parameters are fixed so output is deterministic.
"""

from __future__ import annotations

import base64
import io

import segno


def qr_data_uri(text: str) -> str:
    """Encode ``text`` as a PNG QR data URI (deterministic)."""
    buffer = io.BytesIO()
    segno.make(text).save(buffer, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
