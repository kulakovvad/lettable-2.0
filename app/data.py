"""Adapters between the UI and the business modules (verify, core.proforma, core.explain,
core.audit, evalq.score, market). All database access goes through core.store.

Facts loaded from fixtures are stored unrouted, so the UI routes current facts in memory with
verify.verify_doc before showing them or building a proforma; routed rows already in the
database are used as they are.
"""

from __future__ import annotations

import getpass
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from core import pdf, store
from core.models import Cell, Document, EvalRow, Fact, Label, ProformaVersion, new_id, valuation_date

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"
MACHINE_EXTRACTORS = ("v7go", "grok", "grok-b")
EXIT_YIELD_STUB_PCT = "5.6"

warnings: list[str] = []


def _warn(where: str, exc: Exception) -> None:
    msg = f"{where}: {type(exc).__name__}: {exc}"
    if msg not in warnings:
        warnings.append(msg)


def backend_status() -> dict[str, str]:
    """Which adapter is live, for the sidebar."""
    out: dict[str, str] = {}
    for label, module in (("verification", "verify"), ("proforma", "core.proforma"),
                          ("explain", "core.explain"), ("audit", "core.audit"),
                          ("evaluation", "evalq.score")):
        try:
            __import__(module)
            out[label] = "real"
        except Exception as exc:
            out[label] = f"missing ({type(exc).__name__})"
    out["exit yield"] = "UI stub assumption"
    for module, name in (("market", "get_exit_yield"), ("market.rca", "exit_yield"),
                         ("market.fallback", "exit_yield")):
        try:
            if hasattr(__import__(module, fromlist=[name]), name):
                out["exit yield"] = f"{module}.{name}"
                break
        except Exception:
            continue
    return out


def change_story(rows: list[dict[str, Any]]) -> str:
    """Plain-text answer to 'why did the value change?' built from diff rows only."""
    if not rows:
        return "No cell changed between these versions."
    r0 = rows[0]
    lines = [f"v{r0.get('version_no', '?')} was created by {r0.get('author')} at "
             f"{str(r0.get('created_at', ''))[:19].replace('T', ' ')} UTC, reason: \"{r0.get('reason')}\"."]
    t = r0.get("trigger_fact")
    if t:
        lines.append(f"Trigger: {t.get('unit_id') or 'property'} {t.get('field')} changed from "
                     f"{t.get('old')} to {t.get('new')} (fact {t.get('fact_id')}; previous value by "
                     f"{t.get('old_author') or 'n/a'}).")
    order = {"value": 0, "total_rent": 1, "wault_expiry": 2, "wault_break": 3}
    for r in sorted(rows, key=lambda r: (order.get(r["name"], 9), r["name"])):
        lines.append(f"- {r['name']}: {fmt_value(r['old'], r.get('unit'))} -> "
                     f"{fmt_value(r['new'], r.get('unit'))}")
    return "\n".join(lines)


def default_author() -> str:
    return os.getenv("TRACELEASE_ANALYST") or getpass.getuser() or "analyst"


# documents and pages

def documents() -> list[Document]:
    return store.documents()


def document(doc_id: str) -> Document | None:
    return store.get_document(doc_id)


def pdf_path(doc: Document) -> Path | None:
    path = PDF_DIR / doc.filename
    return path if path.exists() else None


@lru_cache(maxsize=64)
def _page_texts(path: str) -> tuple[str, ...]:
    return tuple(pdf.page_texts(path))


def page_texts(doc: Document) -> list[str]:
    path = pdf_path(doc)
    return list(_page_texts(str(path))) if path else []


# facts

@lru_cache(maxsize=32)
def _routed(doc_id: str, fact_ids: tuple[str, ...]) -> tuple[Fact, ...]:
    from verify import verify_doc

    doc = store.get_document(doc_id)
    facts = store.current_facts(doc_id=doc_id)
    by_ext: dict[str, list[Fact]] = {}
    others: list[Fact] = []
    for f in facts:
        if f.extractor in MACHINE_EXTRACTORS:
            by_ext.setdefault(f.extractor, []).append(f)
        else:
            others.append(f)
    path = pdf_path(doc) if doc else None
    texts = page_texts(doc) if doc else []
    return tuple(verify_doc(by_ext, texts, str(path) if path else None)) + tuple(others)


def routed_facts(doc_id: str) -> list[Fact]:
    """Current facts of the document with grounded / date_check / review_status filled in."""
    facts = store.current_facts(doc_id=doc_id)
    machine = [f for f in facts if f.extractor in MACHINE_EXTRACTORS]
    if all(f.review_status is not None for f in machine):
        return facts
    return list(_routed(doc_id, tuple(sorted(f.id for f in facts))))


def fact(fact_id: str) -> Fact | None:
    return store.get_fact(fact_id)


def history(fact_id: str) -> list[Fact]:
    from core.audit import history as audit_history

    return audit_history(fact_id)


def review_queue(doc_id: str) -> list[dict[str, Any]]:
    from core.audit import review_groups

    return review_groups(routed_facts(doc_id))


# proforma

def _yield_from_fact(f: Fact) -> dict[str, Any]:
    if store.get_fact(f.id) is None:
        store.insert_facts([f])
    return {"value": f.value, "snapshot_id": f.snapshot_id, "source_fact_id": f.id,
            "author": f.author, "reason": f.reason or f.quote}


def _exit_yield(doc_id: str, facts: list[Fact]) -> dict[str, Any]:
    property_type = next((f.value for f in facts if f.field == "property_type" and f.value), None)
    try:
        from market import get_exit_yield  # type: ignore[attr-defined]

        ey = get_exit_yield(doc_id, property_type)
        if ey:
            return _yield_from_fact(ey) if isinstance(ey, Fact) else ey
    except ImportError:
        pass
    except Exception as exc:
        _warn("market.get_exit_yield", exc)
    for module, arg in (("market.rca", property_type), ("market.fallback", doc_id)):
        try:
            fn = getattr(__import__(module, fromlist=["exit_yield"]), "exit_yield")
        except (ImportError, AttributeError):
            continue
        try:
            f = fn(arg)
            if isinstance(f, Fact) and f.value is not None:
                return _yield_from_fact(f)
        except Exception as exc:
            _warn(f"{module}.exit_yield", exc)
    return {
        "value": float(os.getenv("EXIT_YIELD_PCT", EXIT_YIELD_STUB_PCT)),
        "author": "analyst assumption (UI stub until market/ lands)",
        "reason": "SEGRO H1 equivalent yield 5.6%, press-release-full-version-final.pdf p. 4",
    }


def versions(doc_id: str) -> list[ProformaVersion]:
    return store.versions(doc_id)


def build_version(doc_id: str, author: str, reason: str,
                  trigger_fact_id: str | None = None) -> ProformaVersion:
    """New proforma version from the routed current facts; keeps the previous exit yield."""
    from core.proforma import compute_cells, exit_yield_from_cells

    facts = routed_facts(doc_id)
    previous = store.versions(doc_id)
    ey = (exit_yield_from_cells(store.cells(previous[-1].id)) if previous else None) \
        or _exit_yield(doc_id, facts)
    version = ProformaVersion(doc_id=doc_id,
                              version_no=(previous[-1].version_no + 1) if previous else 1,
                              trigger_fact_id=trigger_fact_id, author=author, reason=reason)
    new_cells = compute_cells(doc_id, facts, valuation_date(), ey, version.id)
    store.new_version(version)
    store.insert_cells(new_cells)
    return version


def cells(version_id: str) -> list[Cell]:
    return store.cells(version_id)


def explain(cell_id: str) -> dict[str, Any]:
    from core.explain import explain as core_explain

    return core_explain(cell_id)


def explain_text(tree: dict[str, Any]) -> str:
    from core.explain import explain_text as core_explain_text

    return core_explain_text(tree)


def fmt_value(value: Any, unit: str | None) -> str:
    from core.explain import fmt_value as core_fmt

    return core_fmt(value, unit)


# manual decisions; each writes a new fact and then a new proforma version

def correct_fact(fact_id: str, value: Any, reason: str, author: str) -> tuple[Fact, ProformaVersion]:
    from core.audit import correct_fact as audit_correct

    new = audit_correct(fact_id, value, reason, author, rebuild=False)
    return new, build_version(new.doc_id, author, reason, new.id)


def confirm_fact(fact_id: str, reason: str, author: str) -> tuple[Fact, ProformaVersion]:
    from core.audit import confirm_fact as audit_confirm

    new = audit_confirm(fact_id, author, reason, rebuild=False)
    return new, build_version(new.doc_id, author, reason, new.id)


def diff(v1_id: str, v2_id: str) -> list[dict[str, Any]]:
    from core.audit import diff as audit_diff

    return audit_diff(v1_id, v2_id)


# evaluation

def labels(doc_ids: list[str]) -> list[Label]:
    return [lb for d in doc_ids for lb in store.labels(doc_id=d)]


def eval_rows(doc_ids: list[str]) -> tuple[list[EvalRow], str]:
    """Latest stored eval run per extractor, else a live in-memory score of the routed facts."""
    stored = [r for r in store.eval_rows() if r.doc_id in doc_ids]
    if stored:
        from evalq.export import latest_rows

        return latest_rows(stored), "stored eval run"
    from evalq.score import score

    facts = [f for d in doc_ids for f in routed_facts(d) if f.extractor in MACHINE_EXTRACTORS]
    lbls = labels(doc_ids)
    run_id = "live-" + new_id()[:8]
    rows: list[EvalRow] = []
    for ext in sorted({f.extractor for f in facts}):
        rows += score(facts, lbls, ext, run_id)
    return rows, "live score of current facts (not stored)"


def eval_summary(rows: list[EvalRow], lbls: list[Label]) -> dict[str, Any]:
    from evalq.score import summary

    return summary(rows, lbls)


def per_doc_run_stats(extractor: str) -> dict[str, Any]:
    runs = store.extraction_runs(extractor)
    docs = {d for r in runs for d in r.doc_ids}
    finished = [r for r in runs if r.finished_at is not None]
    seconds = sum((r.finished_at - r.started_at).total_seconds() for r in finished)
    costs = [float(r.cost) for r in runs if r.cost is not None]
    n = len(docs)
    return {"runs": len(runs), "docs": n,
            "cost_per_doc": sum(costs) / n if n and costs else None,
            "seconds_per_doc": seconds / n if n and finished else None}
