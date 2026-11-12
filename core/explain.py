"""Explain a proforma figure: deterministic walk of the provenance graph, no LLM."""

from __future__ import annotations

from typing import Any, Callable

from core.models import Cell, Fact, Snapshot
from core.proforma import SNAPSHOT_RE

Node = dict[str, Any]
FactLookup = Callable[[str], Fact | None]
SnapshotLookup = Callable[[str], Snapshot | None]


def _snapshot_node(s: Snapshot) -> Node:
    return {
        "kind": "snapshot",
        "id": s.id,
        "name": f"{s.provider} snapshot",
        "value": s.row_count,
        "unit": "rows",
        "formula": s.query_sql,
        "author": s.provider,
        "created_at": s.executed_at.isoformat(),
        "reason": f"result sha256 {s.result_sha256}",
        "doc_id": None,
        "page": None,
        "quote": None,
        "bbox": None,
        "children": [],
    }


def _fact_node(f: Fact, get_snapshot: SnapshotLookup) -> Node:
    children: list[Node] = []
    if f.snapshot_id:
        s = get_snapshot(f.snapshot_id)
        if s is not None:
            children.append(_snapshot_node(s))
    return {
        "kind": "fact",
        "id": f.id,
        "name": f"{f.field}[{f.unit_id}]" if f.unit_id else f.field,
        "value": f.value,
        "unit": f.unit,
        "formula": None,
        "author": f.author,
        "created_at": f.created_at.isoformat(),
        "reason": f.reason,
        "doc_id": f.doc_id,
        "page": f.page,
        "quote": f.quote,
        "bbox": f.bbox,
        "extractor": f.extractor,
        "review_status": f.review_status,
        "supersedes_id": f.supersedes_id,
        "children": children,
    }


def build_tree(cell: Cell, cells_by_id: dict[str, Cell], get_fact: FactLookup,
               get_snapshot: SnapshotLookup) -> Node:
    """Pure tree builder; lookups are injected so tests run without the database."""
    children: list[Node] = []
    for cid in cell.input_cell_ids:
        child = cells_by_id.get(cid)
        if child is not None:
            children.append(build_tree(child, cells_by_id, get_fact, get_snapshot))
    for fid in cell.input_fact_ids:
        f = get_fact(fid)
        if f is not None:
            children.append(_fact_node(f, get_snapshot))
    m = SNAPSHOT_RE.search(cell.formula)
    if m:
        s = get_snapshot(m.group(1).rstrip(")"))
        if s is not None:
            children.append(_snapshot_node(s))
    return {
        "kind": "cell",
        "id": cell.id,
        "name": cell.name,
        "value": cell.value,
        "unit": cell.unit,
        "formula": cell.formula,
        "author": cell.author,
        "created_at": cell.computed_at.isoformat(),
        "reason": cell.reason,
        "doc_id": None,
        "page": None,
        "quote": None,
        "bbox": None,
        "version_id": cell.version_id,
        "children": children,
    }


def explain(cell_id: str) -> Node:
    from core import store

    cell = store.get_cell(cell_id)
    if cell is None:
        raise KeyError(f"cell {cell_id} not found")
    cells_by_id = {c.id: c for c in store.cells(cell.version_id)}
    facts: dict[str, Fact | None] = {}
    snaps: dict[str, Snapshot | None] = {}

    def get_fact(fid: str) -> Fact | None:
        if fid not in facts:
            facts[fid] = store.get_fact(fid)
        return facts[fid]

    def get_snapshot(sid: str) -> Snapshot | None:
        if sid not in snaps:
            snaps[sid] = store.get_snapshot(sid)
        return snaps[sid]

    return build_tree(cell, cells_by_id, get_fact, get_snapshot)


def explain_by_name(version_id: str, name: str) -> Node:
    from core import store

    for c in store.cells(version_id):
        if c.name == name:
            return explain(c.id)
    raise KeyError(f"cell {name!r} not found in version {version_id}")


def fmt_value(value: Any, unit: str | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if unit == "GBP/yr":
            return f"£{value:,.0f} p.a."
        if unit == "GBP":
            return f"£{value:,.0f}"
        if unit == "GBP/sqft/yr":
            return f"£{value:,.2f} psf"
        if unit == "sqft":
            return f"{value:,.0f} sq ft"
        if unit == "years":
            return f"{value:.2f} years"
        if unit == "pct":
            return f"{value:g}%"
    return str(value)


def _is_assumption(node: Node) -> bool:
    return node["kind"] == "cell" and str(node["formula"]).startswith("assumption")


def _sources(node: Node, seen: set[str]) -> list[Node]:
    """Facts, snapshots and assumption cells under node, depth-first, each once."""
    out: list[Node] = []
    for ch in node["children"]:
        if (ch["kind"] != "cell" or _is_assumption(ch)) and ch["id"] not in seen:
            seen.add(ch["id"])
            out.append(ch)
        out.extend(_sources(ch, seen))
    return out


def explain_text(tree: Node) -> str:
    lines = [f"{tree['name']} = {fmt_value(tree['value'], tree['unit'])}, "
             f"computed as {tree['formula']}."]
    if tree.get("reason"):
        lines.append(f'Set by {tree["author"]}: "{tree["reason"]}".')
    direct = [ch for ch in tree["children"] if ch["kind"] == "cell"]
    if direct:
        lines.append("Inputs: " + "; ".join(
            f"{ch['name']} = {fmt_value(ch['value'], ch['unit'])}" for ch in direct) + ".")
    sources = _sources(tree, set())
    if sources:
        lines.append("Sources:")
    for s in sources:
        if s["kind"] == "fact":
            where = f"doc {s['doc_id']}" + (f", page {s['page']}" if s["page"] else "")
            extra = f', quote "{s["quote"]}"' if s["quote"] else ""
            extra += f', reason "{s["reason"]}"' if s["reason"] else ""
            lines.append(f"- {s['name']} = {fmt_value(s['value'], s['unit'])}: fact {s['id']} "
                         f"by {s['author']}, {where}{extra}")
        elif s["kind"] == "snapshot":
            lines.append(f"- {s['name']} {s['id']}: {s['value']} rows, {s['reason']}")
        else:
            why = f', reason "{s["reason"]}"' if s["reason"] else ""
            lines.append(f"- assumption {s['name']} = {fmt_value(s['value'], s['unit'])}, "
                         f"set by {s['author']}{why}")
    return "\n".join(lines)
