"""Shared file-attachment plumbing (validation + response headers).

One implementation for every module that stores user files on disk
(inventory mill certificates, employee documents, future drawing files):
an exact mime allowlist — the client's Content-Type is untrusted input —
and response media types derived from OUR stored extension, never replayed
from the client. Extracted from the inventory module during the EM build.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_FILE_BYTES = 15 * 1024 * 1024  # per file; images are compressed client-side

ALLOWED_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/tiff": ".tif",
    "image/heic": ".heic", "image/heif": ".heif", "application/pdf": ".pdf",
}
_EXT_MIME = {ext: mime for mime, ext in ALLOWED_MIME.items()}
_EXT_MIME[".jpeg"] = "image/jpeg"
_EXT_MIME[".tiff"] = "image/tiff"


def validate_attachment(mime: str, content: bytes) -> str:
    """Return the normalised mime or raise ValueError (user mistake -> 400)."""
    mime = (mime or "").strip().lower()
    if mime not in ALLOWED_MIME:
        raise ValueError(f"Only images and PDF files are allowed (got {mime or 'unknown type'})")
    if not content:
        raise ValueError("The file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("File is larger than 15 MB — scan at a lower resolution")
    return mime


def storage_ext(filename: str, mime: str) -> str:
    """Extension for the on-disk name: the original's if recognisable, else
    derived from the (already validated) mime."""
    ext = Path(filename or "").suffix.lower()
    return ext if ext in _EXT_MIME else ALLOWED_MIME[mime]


def response_mime(stored_name: str) -> str:
    return _EXT_MIME.get(Path(stored_name).suffix.lower(), "application/octet-stream")


def header_filename(filename: str) -> str:
    """Sanitise a user-supplied filename for a Content-Disposition header."""
    return re.sub(r'[^A-Za-z0-9._ ()\-]', "_", filename or "")[:120] or "file"
