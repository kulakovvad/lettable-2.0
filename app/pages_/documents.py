"""Documents & fields: every extracted value with its quote and page, and the highlighted page."""

from __future__ import annotations

import streamlit as st

from app import data
from app.components.pdf_view import frame, selected_row, show_page, show_value


def render(doc_id: str) -> None:
    facts = data.routed_facts(doc_id)
    if not facts:
        st.info("No facts for this document yet.")
        return
    extractors = sorted({f.extractor for f in facts})
    c1, c2, c3 = st.columns([2, 2, 3])
    chosen = c1.multiselect("Extractor", extractors, default=extractors, key="docs_extractors")
    statuses = c2.multiselect("Routing", ["auto", "review", "none"], default=["auto", "review", "none"],
                              key="docs_status")
    search = c3.text_input("Filter by unit or field", key="docs_search").strip().casefold()

    shown = [f for f in facts
             if f.extractor in chosen
             and (f.review_status or "none") in statuses
             and (not search or search in f"{f.unit_id or 'property'} {f.field}".casefold())]
    shown.sort(key=lambda f: (f.unit_id or "", f.field, f.extractor))
    st.caption(f"{len(shown)} of {len(facts)} current facts. Grounded = quote found on the page; "
               "only grounded, auto-routed or human-confirmed values enter the proforma.")

    left, right = st.columns([3, 2])
    with left:
        event = st.dataframe(
            frame([{
                "unit": f.unit_id or "(property)",
                "field": f.field,
                "value": show_value(f.value),
                "page": f.page,
                "quote": f.quote,
                "extractor": f.extractor,
                "grounded": f.grounded,
                "review_status": f.review_status,
                "review_reason": f.review_reason,
            } for f in shown]),
            hide_index=True, on_select="rerun", selection_mode="single-row",
            key="docs_table", height=560,
        )
    with right:
        idx = selected_row(event)
        if idx is None or idx >= len(shown):
            st.info("Select a row to see the source page with the quote outlined.")
            return
        f = shown[idx]
        st.markdown(f"**{f.unit_id or 'Property'} · {f.field} = {show_value(f.value)}**")
        st.caption(f"author {f.author} · run {f.run_id or '-'} · created {f.created_at:%d/%m/%Y %H:%M} UTC")
        if f.quote:
            st.markdown(f"> {f.quote}")
        if f.reason:
            st.markdown(f"Reason: _{f.reason}_")
        show_page(f.doc_id, f.page, f.quote, f.bbox)
