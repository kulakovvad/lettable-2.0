"""PDF helpers shared by extraction, verification and the UI. Pages are 1-based everywhere."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz

HIGHLIGHT_RGB = (1.0, 0.85, 0.0)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def page_count(path: str | Path) -> int:
    with fitz.open(path) as doc:
        return doc.page_count


def page_texts(path: str | Path) -> list[str]:
    """Text of each page, block by block in reading order; element i is page i + 1.

    Blocks keep table cells together, which plain get_text() interleaves column by column.
    """
    out: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            blocks = page.get_text("blocks", sort=True)
            texts = [b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()]
            out.append("\n\n".join(texts))
    return out


def find_bbox(path: str | Path, page: int, quote: str) -> list[float] | None:
    """Union rectangle [x0, y0, x1, y1] of the quote on the page, or None if not found.

    Multi-line quotes rarely match verbatim, so fall back to the longest single line.
    """
    if not quote or not quote.strip():
        return None
    with fitz.open(path) as doc:
        if not 1 <= page <= doc.page_count:
            return None
        pg = doc[page - 1]
        candidates = [" ".join(quote.split())]
        lines = sorted((ln.strip() for ln in quote.splitlines() if ln.strip()), key=len, reverse=True)
        candidates += lines
        for cand in candidates:
            rects = pg.search_for(cand)
            if rects:
                r = rects[0]
                for extra in rects[1:]:
                    r |= extra
                return [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
    return None


def render_page(path: str | Path, page: int, bbox: list[float] | None = None,
                zoom: float = 2.0) -> bytes:
    """PNG bytes of the page, with the bbox outlined when given."""
    with fitz.open(path) as doc:
        pg = doc[page - 1]
        if bbox:
            rect = fitz.Rect(*bbox) + (-2, -2, 2, 2)
            annot = pg.add_rect_annot(rect)
            annot.set_colors(stroke=HIGHLIGHT_RGB)
            annot.set_border(width=2)
            annot.update()
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)
        return pix.tobytes("png")
