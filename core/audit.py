"""Human corrections and confirmations, proforma recompute, version diff and fact history.

Every change is a new fact row with `supersedes_id`; nothing is updated or deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import DATE_FIELDS, FIELD_TYPES, Cell, Fact, ProformaVersion, Value
from core.proforma import is_human, unit_key


def coerce_value(field: str, value: Any) -> Value:
    """Cast a value typed by a person to the field's type. UK dates are read day first."""
    if value is None:
        return None
    kind = FIELD_TYPES.get(field, str)
    if field in DATE_FIELDS:
        s = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        raise ValueError(f"{field}: cannot read date {value!r}; use dd/mm/yyyy")
    if kind in (int, float):
        s = str(value).replace(",", "").replace("£", "").replace("%", "").strip()
        num = float(s)
        return int(round(num)) if kind is int else num
    return str(value).strip()


def _head(fact_id: str) -> Fact:
    from core import store

    chain = store.fact_history(fact_id)
    if not chain:
        raise KeyError(f"fact {fact_id} not found")
    superseded = {f.supersedes_id for f in chain if f.supersedes_id}
    heads = [f for f in chain if f.id not in superseded]
    return max(heads, key=lambda f: f.created_at)


def human_fact(base: Fact, value: Value, reason: str, author: str) -> Fact:
    """Pure: the new row that supersedes `base`."""
    if not (reason or "").strip():
        raise ValueError("a correction needs a reason")
    if not (author or "").strip():
        raise ValueError("a correction needs an author")
    return Fact(
        doc_id=base.doc_id,
        unit_id=base.unit_id,
        field=base.field,
        value=coerce_value(base.field, value),
        unit=base.unit,
        review_status="auto",
        extractor="grok-bot" if author.startswith("grok-bot:") else "manual",
        author=author,
        supersedes_id=base.id,
        reason=reason.strip(),
    )


def recompute(doc_id: str, trigger_fact_id: str | None, author: str,
              reason: str | None) -> ProformaVersion:
    from core.proforma import build_version

    return build_version(doc_id, author, reason, trigger_fact_id=trigger_fact_id)


def correct_fact(fact_id: str, value: Any, reason: str, author: str,
                 rebuild: bool = True) -> Fact:
    if not (reason or "").strip():
        raise ValueError("a correction needs a reason")
    from core import store

    new = human_fact(_head(fact_id), value, reason, author)
    store.insert_facts([new])
    if rebuild:
        recompute(new.doc_id, new.id, author, reason)
    return new


def confirm_fact(fact_id: str, author: str, reason: str = "confirmed in review",
                 rebuild: bool = True) -> Fact:
    head = _head(fact_id)
    return correct_fact(head.id, head.value, reason, author, rebuild=rebuild)


def review_groups(facts: list[Fact]) -> list[dict[str, Any]]:
    """Pure: current review facts grouped by (doc, unit, field), skipping groups a person resolved."""
    superseded = {f.supersedes_id for f in facts if f.supersedes_id}
    heads = [f for f in facts if f.id not in superseded]
    groups: dict[tuple[str, str | None, str], list[Fact]] = {}
    for f in heads:
        groups.setdefault((f.doc_id, unit_key(f.unit_id), f.field), []).append(f)
    out: list[dict[str, Any]] = []
    for (doc_id, _, field), group in groups.items():
        if any(is_human(f) for f in group) or not any(f.review_status == "review" for f in group):
            continue
        reasons = sorted({f.review_reason for f in group if f.review_reason})
        out.append({
            "doc_id": doc_id,
            "unit_id": next((f.unit_id for f in group if f.unit_id), None),
            "field": field,
            "reasons": reasons,
            "options": [
                {
                    "fact_id": f.id,
                    "extractor": f.extractor,
                    "author": f.author,
                    "value": f.value,
                    "quote": f.quote,
                    "page": f.page,
                    "bbox": f.bbox,
                    "grounded": f.grounded,
                    "date_check": f.date_check,
                    "review_status": f.review_status,
                    "review_reason": f.review_reason,
                }
                for f in sorted(group, key=lambda f: f.extractor)
            ],
        })
    return sorted(out, key=lambda g: (g["doc_id"], g["unit_id"] or "", g["field"]))


def review_queue(doc_id: str | None = None) -> list[dict[str, Any]]:
    from core import store

    return review_groups(store.current_facts(doc_id=doc_id))


def diff_cells(old: list[Cell], new: list[Cell]) -> list[dict[str, Any]]:
    """Pure: cells whose value changed between two versions, matched by name."""
    a = {c.name: c for c in old}
    b = {c.name: c for c in new}
    out: list[dict[str, Any]] = []
    for name in sorted(set(a) | set(b)):
        ov = a[name].value if name in a else None
        nv = b[name].value if name in b else None
        if ov == nv and (name in a) == (name in b):
            continue
        delta = None
        if isinstance(ov, (int, float)) and isinstance(nv, (int, float)):
            delta = round(nv - ov, 4)
        out.append({"name": name, "old": ov, "new": nv, "delta": delta,
                    "unit": (b.get(name) or a.get(name)).unit})
    return out


def diff(v1_id: str, v2_id: str) -> list[dict[str, Any]]:
    from core import store

    v2 = store.get_version(v2_id)
    if v2 is None or store.get_version(v1_id) is None:
        raise KeyError("version not found")
    trigger = None
    if v2.trigger_fact_id:
        f = store.get_fact(v2.trigger_fact_id)
        if f is not None:
            prev = store.get_fact(f.supersedes_id) if f.supersedes_id else None
            trigger = {
                "fact_id": f.id,
                "unit_id": f.unit_id,
                "field": f.field,
                "old": prev.value if prev else None,
                "new": f.value,
                "old_fact_id": prev.id if prev else None,
                "old_author": prev.author if prev else None,
            }
    rows = diff_cells(store.cells(v1_id), store.cells(v2_id))
    for r in rows:
        r.update({"trigger_fact": trigger, "author": v2.author, "reason": v2.reason,
                  "created_at": v2.created_at.isoformat(), "version_no": v2.version_no})
    return rows


def history(fact_id: str) -> list[Fact]:
    from core import store

    return store.fact_history(fact_id)
