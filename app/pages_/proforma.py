"""Proforma and Explain: rent roll, key cells, and the provenance tree down to the PDF page."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from app import data
from app.components.pdf_view import frame, show_page, show_value
from core.models import Cell

KEY_CELLS = ("total_rent", "total_area", "rent_psf", "wault_expiry", "wault_break",
             "exit_yield", "value")
KEY_LABELS = {"total_rent": "Total passing rent", "total_area": "Total area",
              "rent_psf": "Rent psf", "wault_expiry": "WAULT to expiry",
              "wault_break": "WAULT to break", "exit_yield": "Exit yield",
              "value": "Gross value"}
UNIT_CELL = re.compile(r"^(\w+)\[(.+)\]$")
ROLL_COLUMNS = ("tenant", "area_sqft", "passing_rent", "lease_expiry", "break_date",
                "years_to_expiry", "years_to_break")


def _rent_roll(cells: list[Cell]) -> list[dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for c in cells:
        m = UNIT_CELL.match(c.name)
        if m and m.group(1) in ROLL_COLUMNS:
            units.setdefault(m.group(2), {"unit": m.group(2)})[m.group(1)] = c.value
    return [{col: show_value(row.get(col)) for col in ("unit", *ROLL_COLUMNS)} for row in units.values()]


def _tree_lines(node: dict[str, Any], depth: int, facts: list[dict[str, Any]]) -> list[str]:
    pad = "    " * depth
    value = data.fmt_value(node.get("value"), node.get("unit"))
    kind = node.get("kind")
    if kind == "fact":
        facts.append(node)
        where = f"page {node['page']}" if node.get("page") else "no page"
        line = (f"{pad}- **fact** `{node['name']}` = {value} · {node.get('extractor', '')} · "
                f"author {node.get('author')} · {str(node.get('created_at', ''))[:16]} · {where}")
        if node.get("quote"):
            line += f" · quote “{' '.join(str(node['quote']).split())}”"
        if node.get("reason"):
            line += f" · reason “{node['reason']}”"
    elif kind == "snapshot":
        line = (f"{pad}- **snapshot** {node['name']} `{node['id']}` · {value} · "
                f"{str(node.get('created_at', ''))[:16]} · {node.get('reason')}")
    else:
        line = f"{pad}- **{node['name']}** = {value} ← `{node.get('formula')}`"
        if node.get("author"):
            line += f" · set by {node['author']}"
        if node.get("reason"):
            line += f" · “{node['reason']}”"
    out = [line]
    for child in node.get("children", []):
        out += _tree_lines(child, depth + 1, facts)
    return out


def _pending(doc_id: str) -> None:
    queue = data.review_queue(doc_id)
    if queue:
        items = ", ".join(f"{q['unit_id'] or 'property'} {q['field']}" for q in queue)
        st.warning(f"Not in the proforma until reviewed ({len(queue)}): {items}. See Review queue.")


def render(doc_id: str, version_id: str | None, author: str) -> None:
    versions = data.versions(doc_id)
    if not versions:
        st.info("No proforma version for this document yet.")
        if st.button("Build proforma v1 from current facts", key="build_v1", type="primary"):
            v = data.build_version(doc_id, author, "initial build from extracted facts")
            st.session_state["pending_version"] = v.id
            st.session_state["flash"] = f"v{v.version_no} created."
            st.rerun()
        return
    version = next((v for v in versions if v.id == version_id), versions[-1])
    cells = data.cells(version.id)
    by_name = {c.name: c for c in cells}
    st.caption(f"Version v{version.version_no} · by {version.author} · "
               f"{version.created_at:%d/%m/%Y %H:%M} UTC · {version.reason or ''}")
    _pending(doc_id)

    cols = st.columns(len(KEY_CELLS))
    for col, name in zip(cols, KEY_CELLS):
        c = by_name.get(name)
        col.metric(KEY_LABELS[name], data.fmt_value(c.value, c.unit) if c else "n/a")
    st.caption("Gross value = total passing rent / exit yield; purchaser's costs are not deducted. "
               "WAULT is rent-weighted from the valuation date "
               f"{by_name['valuation_date'].value if 'valuation_date' in by_name else ''}.")

    st.subheader("Rent roll")
    st.dataframe(frame(_rent_roll(cells)), hide_index=True, key="rent_roll")

    st.subheader("Explain a figure")
    names = [n for n in KEY_CELLS if n in by_name] + sorted(n for n in by_name if n not in KEY_CELLS)
    picked = st.selectbox("Cell", names, key=f"explain_cell_{version.id}",
                          index=names.index("value") if "value" in names else 0,
                          format_func=lambda n: f"{KEY_LABELS.get(n, n)} = "
                                                f"{data.fmt_value(by_name[n].value, by_name[n].unit)}")
    if not picked:
        return
    try:
        tree = data.explain(by_name[picked].id)
    except Exception as exc:
        st.error(f"Explain failed: {type(exc).__name__}: {exc}")
        return
    facts: list[dict[str, Any]] = []
    lines = _tree_lines(tree, 0, facts)
    left, right = st.columns([3, 2])
    with left:
        st.markdown("\n".join(lines))
        with st.expander("Plain-text explanation"):
            st.text(data.explain_text(tree))
    with right:
        sourced = [f for f in facts if f.get("page")]
        if not sourced:
            st.info("No document source under this cell (assumption or human input).")
            return
        unique = list({f["id"]: f for f in sourced}.values())
        chosen = st.selectbox("Source fact", range(len(unique)), key=f"explain_fact_{picked}_{version.id}",
                              format_func=lambda i: f"{unique[i]['name']} = "
                                                    f"{data.fmt_value(unique[i]['value'], unique[i]['unit'])}"
                                                    f" · p. {unique[i]['page']}")
        f = unique[chosen]
        show_page(f["doc_id"], f["page"], f.get("quote"), f.get("bbox"))
