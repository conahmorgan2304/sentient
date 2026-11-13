"""Optional OCR of image-only PDF pages on the *local* Home Assistant host.

No network calls, packages or binaries are downloaded. On HA OS the Tesseract
executable may be absent: return 'unavailable' and keep the existing PDF
fallback. This must be opted into separately from reading text PDF pages.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any

MAX_IMAGE_BYTES = 4_000_000
MAX_IMAGE_PIXELS = 16_000_000
MAX_OCR_CHARS = 8000


def available() -> bool:
    return bool(shutil.which("tesseract"))


def extract_page(page: Any, *, timeout: float = 6.0) -> str:
    """Run bounded local OCR on at most two embedded PDF images.

    Called from Home Assistant's executor, never the event loop.
    """
    command = shutil.which("tesseract")
    if not command:
        return ""
    texts = []
    try:
        embedded = page.images
    except Exception:
        return ""
    for image in list(embedded)[:2]:
        try:
            picture = image.image
            if picture.width * picture.height > MAX_IMAGE_PIXELS:
                continue
            raw = image.data
            if not isinstance(raw, bytes) or len(raw) > MAX_IMAGE_BYTES:
                continue
            result = subprocess.run(
                [command, "stdin", "stdout", "--psm", "3"],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False, timeout=timeout,
            )
            if result.returncode == 0:
                texts.append(result.stdout[:MAX_OCR_CHARS].decode("utf-8", "replace"))
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            continue
    return "\n".join(texts)[:MAX_OCR_CHARS]
