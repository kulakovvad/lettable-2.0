"""Evaluation: V7 Go vs Grok against the automatic reference, error table, page of each error."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app import data
from app.components.pdf_view import frame, selected_row, show_page, show_value

ERROR_VERDICTS = ["missed", "wrong", "invented", "ungrounded"]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.4f}"


def _secs(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.1f} s"


def _reference_caption(doc_ids: list[str]) -> None:
    lbls = data.labels(doc_ids)
    agreed = sum(1 for lb in lbls if lb.status == "agreed")
    arbiter = sum(1 for lb in lbls if lb.label_source == "arbiter")
    share = f"{agreed / len(lbls) * 100:.1f}%" if lbls else "n/a"
    st.caption(f"Reference: automatic (table parser + self-check, + Grok arbiter on disputed labels), "
               f"not hand labelled. {len(lbls)} labels on {len(doc_ids)} document(s); agreed "
               f"{agreed} ({share}); from arbiter {arbiter}.")


def _cards(summary: dict[str, Any], extractors: list[str]) -> None:
    for ext in extractors:
        block = summary["all"].get(ext)
        if not block:
            continue
        auto = block["by_review_status"]["auto"]
        review = block["by_review_status"]["review"]
        runs = data.per_doc_run_stats(ext)
        st.markdown(f"**{ext}** · sample: {block['sample']['documents']} document(s), "
                    f"{block['sample']['units']} units, {block['sample']['labelled_fields']} labelled fields, "
                    f"{block['sample']['facts']} facts")
        cols = st.columns(6)
        cols[0].metric("Auto-accepted fields", _pct(block["auto_share"]),
                       help=f"n = {block['sample']['facts']} facts")
        cols[1].metric("Precision in auto group", _pct(auto["precision"]), help=f"n = {auto['n']}")
        cols[2].metric("Precision in review group", _pct(review["precision"]), help=f"n = {review['n']}")
        cols[3].metric("Recall overall", _pct(block["overall"]["recall"]),
                       help=f"n = {block['overall']['n']}")
        cols[4].metric("Cost per brochure", _money(runs["cost_per_doc"]),
                       help=f"{runs['runs']} run(s) over {runs['docs']} document(s)")
        cols[5].metric("Time per brochure", _secs(runs["seconds_per_doc"]),
                       help=f"{runs['runs']} run(s) over {runs['docs']} document(s)")


def _by_field(summary: dict[str, Any], extractors: list[str], scope: str) -> None:
    blocks = summary.get(scope) or {}
    fields = sorted({f for e in extractors for f in (blocks.get(e) or {}).get("by_field", {})})
    rows = []
    for field in fields:
        row: dict[str, Any] = {"field": field}
        for ext in extractors:
            s = (blocks.get(ext) or {}).get("by_field", {}).get(field)
            row[f"{ext} precision"] = _pct(s["precision"]) if s else ""
            row[f"{ext} recall"] = _pct(s["recall"]) if s else ""
            row[f"{ext} n"] = s["n"] if s else 0
            row[f"{ext} errors"] = (sum(s[v] for v in ERROR_VERDICTS) if s else 0)
        rows.append(row)
    st.dataframe(frame(rows), hide_index=True, key=f"eval_fields_{scope}")


def render(doc_ids: list[str]) -> None:
    rows, source = data.eval_rows(doc_ids)
    if not rows:
        st.info("No evaluation rows: no labels or no extracted facts for these documents yet.")
        return
    lbls = data.labels(doc_ids)
    summary = data.eval_summary(rows, lbls)
    extractors = sorted(summary["all"])
    st.caption(f"Source: {source}. Every figure below is computed from the database.")
    _reference_caption(doc_ids)
    _cards(summary, extractors)

    st.subheader("Precision and recall by field")
    scope = st.radio("Reference subset", ["all", "agreed"], horizontal=True, key="eval_scope",
                     format_func=lambda s: "all labels" if s == "all" else "agreed labels only")
    if scope == "agreed" and summary.get("agreed") is None:
        st.info("Agreed split unavailable.")
    else:
        _by_field(summary, extractors, scope)

    st.subheader("Errors")
    verdicts = st.multiselect("Verdict", ERROR_VERDICTS, default=ERROR_VERDICTS, key="eval_verdicts")
    errors = sorted((r for r in rows if r.verdict in verdicts),
                    key=lambda r: (r.doc_id, r.unit_id or "", r.field, r.extractor))
    left, right = st.columns([3, 2])
    with left:
        event = st.dataframe(
            frame([{
                "extractor": r.extractor, "doc": r.doc_id, "unit": r.unit_id or "(property)",
                "field": r.field, "expected": show_value(r.expected), "got": show_value(r.got),
                "verdict": r.verdict, "routing": r.review_status,
            } for r in errors]),
            hide_index=True, on_select="rerun", selection_mode="single-row", key="eval_errors",
        )
        st.caption(f"{len(errors)} error row(s) of {len(rows)} scored rows.")
    with right:
        idx = selected_row(event)
        if idx is None or idx >= len(errors):
            st.info("Select an error to see the page it came from.")
            return
        r = errors[idx]
        f = data.fact(r.fact_id) if r.fact_id else None
        lb = next((x for x in lbls if x.id == r.label_id), None)
        st.markdown(f"**{r.extractor} · {r.unit_id or 'Property'} · {r.field} · {r.verdict}**")
        if f is not None:
            routed = next((x for x in data.routed_facts(f.doc_id) if x.id == f.id), f)
            st.markdown(f"Extractor quote, page {f.page}:")
            st.markdown(f"> {f.quote or '(none)'}")
            st.caption(f"grounded: {routed.grounded} · routing: {routed.review_status} "
                       f"({routed.review_reason or '-'})")
        if lb is not None:
            st.markdown(f"Reference ({lb.label_source}, {lb.status}), page {lb.page}:")
            st.markdown(f"> {lb.quote or '(none)'}")
        if f is not None and f.page and (routed.grounded is not False or lb is None):
            show_page(f.doc_id, f.page, f.quote, routed.bbox)
        elif lb is not None:
            show_page(lb.doc_id, lb.page, lb.quote,
                      caption=f"Reference location · page {lb.page}")
        elif f is not None:
            show_page(f.doc_id, f.page, f.quote)
