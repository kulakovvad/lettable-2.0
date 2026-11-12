"""Review queue: both extractor variants with quotes and the routing reason; confirm or correct."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app import data
from app.components.pdf_view import frame, selected_row, show_page, show_value
from core.models import DATE_FIELDS

REASON_TEXT = {
    "extractors_disagree": "extractors disagree",
    "ungrounded": "quote not found on the page",
    "date_inconsistent": "dates do not reconcile",
    "number_not_in_quote": "number not in the quote",
    "missing_in_one": "only one extractor found it",
}


def _done(version_no: int, version_id: str, what: str) -> None:
    st.session_state["pending_version"] = version_id
    st.session_state["flash"] = f"v{version_no} created: {what}"
    st.rerun()


def _act(fact_id: str, field: str, author: str, key: str, default_value: Any) -> None:
    hint = "dd/mm/yyyy" if field in DATE_FIELDS else "value"
    c1, c2 = st.columns(2)
    with c1:
        reason_confirm = st.text_input("Reason for confirming", key=f"{key}_creason",
                                       placeholder="e.g. checked against the footnote on page 3")
        if st.button("Confirm this value", key=f"{key}_confirm", disabled=not reason_confirm.strip(),
                     type="primary"):
            try:
                new, v = data.confirm_fact(fact_id, reason_confirm, author)
                _done(v.version_no, v.id, f"{new.unit_id or 'property'} {new.field} confirmed by {author}")
            except Exception as exc:
                st.error(f"Confirm failed: {type(exc).__name__}: {exc}")
    with c2:
        value = st.text_input(f"New value ({hint})", key=f"{key}_value",
                              value=show_value(default_value))
        reason = st.text_input("Reason for the correction (required)", key=f"{key}_reason")
        if st.button("Correct", key=f"{key}_correct", disabled=not (reason.strip() and value.strip())):
            try:
                new, v = data.correct_fact(fact_id, value, reason, author)
                _done(v.version_no, v.id,
                      f"{new.unit_id or 'property'} {new.field} = {show_value(new.value)} by {author}")
            except Exception as exc:
                st.error(f"Correction rejected: {type(exc).__name__}: {exc}")
    st.caption("Press Enter after typing so the button picks up the reason. "
               "Both actions append a new fact and build a new proforma version; nothing is overwritten.")


def _queue(doc_id: str, author: str) -> None:
    queue = data.review_queue(doc_id)
    if not queue:
        st.success("Review queue is empty for this document.")
        return
    event = st.dataframe(
        frame([{
            "unit": q["unit_id"] or "(property)", "field": q["field"],
            "why": ", ".join(REASON_TEXT.get(r, r) for r in q["reasons"]),
            "variants": " | ".join(f"{o['extractor']}: {show_value(o['value'])}" for o in q["options"]),
        } for q in queue]),
        hide_index=True, on_select="rerun", selection_mode="single-row", key="review_table",
    )
    idx = selected_row(event)
    item = queue[idx if idx is not None and idx < len(queue) else 0]
    st.markdown(f"#### {item['unit_id'] or 'Property'} · {item['field']}")
    st.caption("In review because: " + ", ".join(REASON_TEXT.get(r, r) for r in item["reasons"]))

    options = item["options"]
    cols = st.columns(len(options))
    for col, o in zip(cols, options):
        with col, st.container(border=True):
            st.markdown(f"**{o['extractor']}**: {show_value(o['value'])}")
            st.caption(f"page {o['page']} · grounded {o['grounded']} · dates {o['date_check']} · "
                       f"{o['author']}")
            st.markdown(f"> {o['quote'] or '(no quote)'}")
    pick = st.radio("Act on variant", range(len(options)), horizontal=True,
                    key=f"review_pick_{item['unit_id']}_{item['field']}",
                    format_func=lambda i: f"{options[i]['extractor']}: {show_value(options[i]['value'])}")
    chosen = options[pick]
    left, right = st.columns([2, 3])
    with left:
        _act(chosen["fact_id"], item["field"], author,
             f"rq_{chosen['fact_id']}", chosen["value"])
    with right:
        show_page(item["doc_id"], chosen["page"], chosen["quote"], chosen.get("bbox"))


def _edit_any(doc_id: str, author: str) -> None:
    facts = sorted(data.routed_facts(doc_id), key=lambda f: (f.unit_id or "", f.field, f.extractor))
    if not facts:
        return
    i = st.selectbox("Fact", range(len(facts)), key="edit_any_fact",
                     format_func=lambda i: f"{facts[i].unit_id or 'property'} · {facts[i].field} · "
                                           f"{facts[i].extractor} = {show_value(facts[i].value)}")
    f = facts[i]
    _act(f.id, f.field, author, f"ea_{f.id}", f.value)


def render(doc_id: str, author: str) -> None:
    if not author.strip():
        st.warning("Enter your name in the sidebar: every decision records its author.")
        return
    _queue(doc_id, author)
    with st.expander("Edit any field (manual correction with a reason)"):
        _edit_any(doc_id, author)
