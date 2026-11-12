"""PDF page with the quote outlined, plus small display helpers shared by the tabs."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from app import data
from core import pdf


@st.cache_data(show_spinner=False, max_entries=128)
def _png(path: str, page: int, bbox: tuple[float, ...] | None) -> bytes:
    return pdf.render_page(path, page, list(bbox) if bbox else None)


@st.cache_data(show_spinner=False, max_entries=512)
def _bbox(path: str, page: int, quote: str) -> tuple[float, ...] | None:
    found = pdf.find_bbox(path, page, quote)
    return tuple(found) if found else None


def show_page(doc_id: str | None, page: int | None, quote: str | None = None,
              bbox: list[float] | None = None, caption: str | None = None) -> None:
    if not doc_id or not page:
        st.info("No page to show: this value has no page reference (human input or missing).")
        return
    doc = data.document(doc_id)
    path = data.pdf_path(doc) if doc else None
    if path is None:
        st.warning(f"PDF not available locally for {doc_id}; expected under data/pdfs/.")
        return
    if not 1 <= page <= doc.page_count:
        st.warning(f"Page {page} is outside the document ({doc.page_count} pages).")
        return
    box = tuple(bbox) if bbox else (_bbox(str(path), page, quote) if quote else None)
    note = "quote outlined" if box else "quote not located on the page"
    st.image(_png(str(path), page, box),
             caption=caption or f"{doc.filename} · page {page} · {note}",
             width="stretch")


def show_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Mixed-type values become strings so Arrow can serialise the column."""
    df = pd.DataFrame(rows)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: show_value(v) if not isinstance(v, str) else v)
    return df


def selected_row(event: Any) -> int | None:
    try:
        rows = event.selection.rows
    except AttributeError:
        return None
    return rows[0] if rows else None
