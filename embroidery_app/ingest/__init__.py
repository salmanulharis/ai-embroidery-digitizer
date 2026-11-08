"""Input detection: decide whether an upload is raster or vector artwork."""

from __future__ import annotations

from pathlib import Path

RASTER_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VECTOR_EXT = {".svg", ".ai", ".pdf"}
UNSUPPORTED_HINTS = {
    ".eps": "EPS is not supported directly - export it as SVG or PDF.",
    ".cdr": "CorelDRAW files are not supported - export as SVG or PDF.",
    ".emb": "Wilcom EMB is a closed format - export from Wilcom as DST or SVG.",
}


def detect_kind(filename: str, data: bytes) -> str:
    """Return 'raster', 'svg' or 'pdf' (AI files are PDF containers)."""
    ext = Path(filename).suffix.lower()
    head = data[:2048].lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if ext == ".svg" or head.startswith(b"<?xml") or b"<svg" in head[:1024]:
        return "svg"
    if ext == ".ai":
        if head.startswith(b"%!PS"):
            raise ValueError(
                "This is a legacy PostScript-only Illustrator file. Re-save it with "
                "'Create PDF Compatible File' enabled, or export it as SVG."
            )
        return "pdf"
    if ext in UNSUPPORTED_HINTS:
        raise ValueError(UNSUPPORTED_HINTS[ext])
    if ext in RASTER_EXT or head[:8] in (b"\x89PNG\r\n\x1a\n",) or head[:3] == b"\xff\xd8\xff" or head[8:12] == b"WEBP":
        return "raster"
    raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Use PNG, JPG, WEBP, SVG, AI or PDF.")
