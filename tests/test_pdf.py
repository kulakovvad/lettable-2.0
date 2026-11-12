from pathlib import Path

import pytest

from core.pdf import find_bbox, page_texts, render_page

PDF = Path(__file__).resolve().parent.parent / "data" / "pdfs" / (
    "acuitus-90f42467_unit-1-to-4-the-hidings-cirencester-gloucestershir-13c9c3.pdf"
)
needs_pdf = pytest.mark.skipif(not PDF.exists(), reason="brochure not available locally")


@needs_pdf
def test_page_texts_reads_tenancy_table() -> None:
    texts = page_texts(PDF)
    assert len(texts) == 17
    assert "Tenancy & Accommodation" in texts[2]
    assert "£44,500" in texts[2]


@needs_pdf
def test_find_bbox_and_render() -> None:
    bbox = find_bbox(PDF, 3, "£44,500")
    assert bbox is not None and len(bbox) == 4
    assert find_bbox(PDF, 3, "not on this page at all") is None
    png = render_page(PDF, 3, bbox, zoom=1.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
