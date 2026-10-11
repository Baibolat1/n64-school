"""Offline QR-code image generation (no external API).

Uses the pure-Python ``qrcode`` library with Pillow for PNG output, so nothing
leaves the server. The image encodes exactly the value the scanner will read
back.
"""

import io

import qrcode

# What the QR payload is prefixed with, so a scan can be recognized as a
# registration code (and a stray QR of some other kind is rejected).
PAYLOAD_PREFIX = "school-qr:"


def encode_payload(raw_token: str) -> str:
    """The exact string placed in the QR code for ``raw_token``."""
    return PAYLOAD_PREFIX + raw_token


def decode_payload(text: str) -> str | None:
    """Return the token from a scanned payload, or ``None`` if it is not ours."""
    text = text.strip()
    if not text.startswith(PAYLOAD_PREFIX):
        return None
    token = text[len(PAYLOAD_PREFIX):].strip()
    return token or None


def make_png(payload: str) -> bytes:
    """Render ``payload`` as a PNG QR code and return the bytes."""
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
