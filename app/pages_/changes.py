"""Changes: cell diff between two proforma versions and the version timeline of a fact."""

from __future__ import annotations

import streamlit as st

from app import data
from app.components.pdf_view import frame, show_value


def _timeline(doc_id: str, default_fact_id: str | None) -> None:
    st.subheader("Fact timeline")
    facts = sorted(data.routed_facts(doc_id), key=lambda f: (f.unit_id or "", f.field, f.extractor))
    if not facts:
        return
    ids = [f.id for f in facts]
    start = ids.index(default_fact_id) if default_fact_id in ids else 0
    i = st.selectbox("Fact", range(len(facts)), index=start, key=f"timeline_fact_{default_fact_id}",
                     format_func=lambda i: f"{facts[i].unit_id or 'property'} · {facts[i].field} · "
                                           f"{facts[i].extractor} = {show_value(facts[i].value)}")
    chain = data.history(facts[i].id)
    st.dataframe(frame([{
        "when (UTC)": f"{h.created_at:%d/%m/%Y %H:%M:%S}", "value": show_value(h.value),
        "author": h.author, "extractor": h.extractor, "reason": h.reason, "page": h.page,
        "quote": h.quote, "supersedes": h.supersedes_id, "id": h.id,
    } for h in chain]), hide_index=True, key="timeline_table")


def render(doc_id: str) -> None:
    versions = data.versions(doc_id)
    if len(versions) < 2:
        st.info(f"{len(versions)} proforma version(s) so far; a diff needs two. Build v1 in Proforma, "
                "then confirm or correct a field in Review queue to create v2.")
        _timeline(doc_id, None)
        return
    st.dataframe(frame([{
        "version": f"v{v.version_no}", "author": v.author, "reason": v.reason,
        "trigger fact": v.trigger_fact_id, "created (UTC)": f"{v.created_at:%d/%m/%Y %H:%M:%S}",
    } for v in versions]), hide_index=True, key="versions_table")
    labels = {v.id: f"v{v.version_no} · {v.author} · {v.created_at:%d/%m %H:%M}" for v in versions}
    ids = [v.id for v in versions]
    c1, c2 = st.columns(2)
    v1 = c1.selectbox("From", ids, index=len(ids) - 2, format_func=labels.get, key=f"diff_v1_{ids[-1]}")
    v2 = c2.selectbox("To", ids, index=len(ids) - 1, format_func=labels.get, key=f"diff_v2_{ids[-1]}")
    if v1 == v2:
        st.info("Pick two different versions.")
        return
    try:
        rows = data.diff(v1, v2)
    except Exception as exc:
        st.error(f"Diff failed: {type(exc).__name__}: {exc}")
        return
    trigger = rows[0].get("trigger_fact") if rows else None
    if trigger:
        st.markdown(f"**Trigger:** {trigger['unit_id'] or 'property'} {trigger['field']}: "
                    f"{show_value(trigger['old'])} → {show_value(trigger['new'])} "
                    f"(was by {trigger.get('old_author') or '-'})")
    if not rows:
        st.info("No cell changed between these versions.")
    else:
        st.dataframe(frame([{
            "cell": r["name"], "old": data.fmt_value(r["old"], r.get("unit")),
            "new": data.fmt_value(r["new"], r.get("unit")),
            "delta": show_value(r["delta"]), "reason": r.get("reason"), "who": r.get("author"),
            "when (UTC)": str(r.get("created_at", ""))[:19].replace("T", " "),
        } for r in rows]), hide_index=True, key="diff_table")
        with st.expander("Why did it change?", expanded=True):
            st.text(data.change_story(rows))
    _timeline(doc_id, trigger["fact_id"] if trigger else None)
