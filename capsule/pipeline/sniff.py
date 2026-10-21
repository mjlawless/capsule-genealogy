"""What the BYTES say a file is — the one magic-byte table, shared by every entry point.

BUG-008: `pdfobject.min.js` was fetched, renamed `.pdf`, and declared
`mime: application/pdf`. It would have passed every hash check and every "the file
exists" check forever, while being neither a PDF nor evidence. CLAUDE.md §12.4: a
checksum proves INTEGRITY, not IDENTITY — an evidence image that is not an image is not
evidence.

So the bytes decide, and a filename or a Content-Type is only a hint. The browser
backend uses this for in-page media; `capsule ingest` (REQ-008) uses it for a file on
disk, where the extension is exactly as untrustworthy as a page's filename.
"""
from __future__ import annotations

from typing import Optional

#: The largest single archived file (a high-res certificate scan fits comfortably).
#: The backend caps each browser-provided media item at this; `capsule ingest` caps
#: the file it reads.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MiB

_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"II*\x00", "tif", "image/tiff"),
    (b"MM\x00*", "tif", "image/tiff"),
)


def sniff(data: bytes) -> Optional[tuple[str, str]]:
    """(ext, mime) as the BYTES declare themselves, or None if we do not recognize them."""
    for signature, ext, mime in _MAGIC:
        if data.startswith(signature):
            return ext, mime
    # RIFF....WEBP — the only one whose signature is not a simple prefix.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None
