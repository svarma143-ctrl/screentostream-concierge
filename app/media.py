"""Small image-header inspector used by the multimodal frame ingest path.

No Pillow dependency is needed for the demo: we only need format, dimensions,
orientation, and a safe aspect-ratio for the model prompt and UI metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from .config import settings

FRAME_PREFIX = "/media/frames/"


def sign_frame_url(path: str, *, ttl: int | None = None) -> str:
    """Return a short-lived, HMAC-signed URL for an uploaded frame.

    Frames are user uploads, so they must not be served by the public static
    mount. The signature is a capability: only a caller that received the URL
    from an authenticated API response can fetch the bytes.
    """
    if not path.startswith(FRAME_PREFIX):
        raise ValueError("Only /media/frames/ paths can be signed.")
    expires = int(time.time()) + (ttl if ttl is not None else settings.frame_url_ttl_seconds)
    signature = _frame_signature(path, expires)
    return f"{path}?exp={expires}&sig={signature}"


def verify_frame_signature(path: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return any(
        hmac.compare_digest(_frame_signature(path, expires, key=key), signature)
        for key in settings.signing_keys
    )


def _frame_signature(path: str, expires: int, *, key: str | None = None) -> str:
    message = f"s2s-frame:{path}:{expires}".encode()
    return hmac.new((key or settings.secret_key).encode(), message, hashlib.sha256).hexdigest()


def image_info(data: bytes, content_type: str | None = None) -> dict[str, Any]:
    fmt, width, height = _inspect(data)
    info: dict[str, Any] = {
        "format": fmt or _format_from_content_type(content_type),
        "width": width,
        "height": height,
        "bytes": len(data),
        "aspect_ratio": round(width / height, 3) if width and height else None,
        "orientation": _orientation(width, height),
    }
    return info


def _format_from_content_type(content_type: str | None) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get((content_type or "").lower(), "unknown")


def _orientation(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _inspect(data: bytes) -> tuple[str | None, int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return "png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return "gif", int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")

    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)

    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        return _webp_size(data)

    return None, None, None


def _jpeg_size(data: bytes) -> tuple[str, int | None, int | None]:
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index + 8 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in (0xD8, 0xD9):
            continue
        if marker == 0xDA:
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in sof_markers:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return "jpeg", width, height
        index += length
    return "jpeg", None, None


def _webp_size(data: bytes) -> tuple[str, int | None, int | None]:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return "webp", width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return "webp", width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return "webp", width, height
    return "webp", None, None
