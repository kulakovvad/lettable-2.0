"""Score extracted facts against reference labels and summarise precision and recall.

Verdicts: ungrounded (grounding failed) > missed (label, no value) > wrong (values differ)
> invented (value, no label) > caught.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.models import ALL_FIELDS, EvalRow, Fact, Label, new_id
from evalq.matching import match_units, values_equal

# The reference parser does not label these, so they are not scored.
UNSCORED_FIELDS = frozenset({"address", "property_type"})
SCORED_FIELDS = tuple(f for f in ALL_FIELDS if f not in UNSCORED_FIELDS)
VERDICTS = ("caught", "missed", "wrong", "invented", "ungrounded")


def _heads(facts: list[Fact]) -> list[Fact]:
    superseded = {f.supersedes_id for f in facts if f.supersedes_id}
    return [f for f in facts if f.id not in superseded]


def score(
    facts: list[Fact],
    labels: list[Label],
    extractor: str | None = None,
    eval_run_id: str | None = None,
) -> list[EvalRow]:
    """Facts of one extractor vs labels. Documents without labels are skipped."""
    run_id = eval_run_id or new_id()
    if extractor is not None:
        facts = [f for f in facts if f.extractor == extractor]
    facts = _heads(facts)
    labels_by_doc: dict[str, list[Label]] = defaultdict(list)
    for lb in labels:
        if lb.field in SCORED_FIELDS:
            labels_by_doc[lb.doc_id].append(lb)
    facts_by_doc: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        if f.field in SCORED_FIELDS:
            facts_by_doc[f.doc_id].append(f)

    rows: list[EvalRow] = []
    for doc_id, doc_labels in labels_by_doc.items():
        doc_facts = facts_by_doc.get(doc_id, [])
        label_units = sorted({lb.unit_id for lb in doc_labels if lb.unit_id is not None})
        fact_units = sorted({f.unit_id for f in doc_facts if f.unit_id is not None})
        unit_map = match_units(fact_units, label_units)

        by_key: dict[tuple[str | None, str], Fact] = {}
        orphans: list[Fact] = []
        for f in doc_facts:
            unit = unit_map.get(f.unit_id) if f.unit_id is not None else None
            if f.unit_id is not None and unit is None:
                orphans.append(f)
                continue
            key = (unit, f.field)
            if key in by_key:
                orphans.append(f)
            else:
                by_key[key] = f

        def row(verdict: str, f: Fact | None, lb: Label | None, unit: str | None,
                field: str) -> EvalRow:
            return EvalRow(
                eval_run_id=run_id,
                extractor=extractor or (f.extractor if f else "unknown"),
                doc_id=doc_id,
                unit_id=unit,
                field=field,
                expected=lb.value if lb else None,
                got=f.value if f else None,
                verdict=verdict,  # type: ignore[arg-type]
                fact_id=f.id if f else None,
                label_id=lb.id if lb else None,
                review_status=f.review_status if f else None,
            )

        for lb in doc_labels:
            f = by_key.pop((lb.unit_id, lb.field), None)
            if f is not None and f.grounded is False:
                verdict = "ungrounded"
            elif f is None or f.value is None:
                verdict = "missed"
            elif not values_equal(lb.field, f.value, lb.value):
                verdict = "wrong"
            else:
                verdict = "caught"
            rows.append(row(verdict, f, lb, lb.unit_id, lb.field))
        for (unit, field), f in by_key.items():
            if f.value is None:
                continue
            rows.append(row("ungrounded" if f.grounded is False else "invented", f, None,
                            unit, field))
        for f in orphans:
            if f.value is None:
                continue
            rows.append(row("ungrounded" if f.grounded is False else "invented", f, None,
                            f.unit_id, f.field))
    return rows


def latest_run_facts(facts: list[Fact]) -> list[Fact]:
    """Facts of the most recent extraction run per (doc, extractor), so reruns are not double counted."""
    latest: dict[tuple[str, str], tuple[Any, str | None]] = {}
    for f in facts:
        key = (f.doc_id, f.extractor)
        if key not in latest or f.created_at > latest[key][0]:
            latest[key] = (f.created_at, f.run_id)
    return [f for f in facts if f.run_id == latest[(f.doc_id, f.extractor)][1]]


def score_doc(doc_id: str, extractor: str, eval_run_id: str | None = None) -> list[EvalRow]:
    """Score one extractor on one document from the store, ignoring later human edits."""
    from core import store

    facts = latest_run_facts(store.all_facts(doc_id=doc_id, extractor=extractor))
    return score(facts, store.labels(doc_id=doc_id), extractor, eval_run_id)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def stats(rows: list[EvalRow]) -> dict[str, Any]:
    c = {v: 0 for v in VERDICTS}
    for r in rows:
        c[r.verdict] += 1
    return {
        **c,
        "n": len(rows),
        "precision": _ratio(c["caught"], c["caught"] + c["wrong"] + c["invented"] + c["ungrounded"]),
        "recall": _ratio(c["caught"], c["caught"] + c["missed"] + c["wrong"]),
    }


def _sample(rows: list[EvalRow]) -> dict[str, int]:
    labelled = [r for r in rows if r.label_id is not None]
    return {
        "documents": len({r.doc_id for r in rows}),
        "units": len({(r.doc_id, r.unit_id) for r in labelled if r.unit_id is not None}),
        "labelled_fields": len(labelled),
        "facts": sum(1 for r in rows if r.fact_id is not None),
    }


def _block(rows: list[EvalRow]) -> dict[str, Any]:
    with_fact = [r for r in rows if r.fact_id is not None]
    auto = [r for r in with_fact if r.review_status == "auto"]
    review = [r for r in with_fact if r.review_status == "review"]
    by_field: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_field[r.field].append(r)
    return {
        "overall": stats(rows),
        "by_field": {f: stats(by_field[f]) for f in SCORED_FIELDS if f in by_field},
        "auto_share": _ratio(len(auto), len(with_fact)),
        "review_share": _ratio(len(review), len(with_fact)),
        "by_review_status": {"auto": stats(auto), "review": stats(review)},
        "sample": _sample(rows),
    }


def summary(rows: list[EvalRow], labels: list[Label] | None = None) -> dict[str, Any]:
    """Per extractor: overall, per field and per routing group, on all labels and on agreed only.

    Without `labels` the agreed split is not available and is returned as None.
    """
    by_ext: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_ext[r.extractor].append(r)
    out: dict[str, Any] = {"all": {e: _block(rs) for e, rs in sorted(by_ext.items())}}
    if labels is None:
        out["agreed"] = None
        return out
    agreed_ids = {lb.id for lb in labels if lb.status == "agreed"}
    known_ids = {lb.id for lb in labels}
    out["agreed"] = {
        e: _block([r for r in rs if r.label_id is None or r.label_id in agreed_ids
                   or r.label_id not in known_ids])
        for e, rs in sorted(by_ext.items())
    }
    return out
