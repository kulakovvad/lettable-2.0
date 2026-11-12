"""End-to-end run from cached extractions: ingest -> verify -> store -> exit yield -> proforma -> eval.

Usage: python -m scripts.run_pipeline --docs <file|N> [--extractors v7go,grok]
`--docs N` takes the first N ids of data/labels/demo_corpus.txt (falls back to data/corpus.txt).
Reads data/facts/<extractor>/<doc_id>.json and data/labels/<doc_id>.json; makes no model calls.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

from core import store
from core.models import Document, ExtractionRun, Fact, Label, new_id
from core.pdf import page_count, page_texts, sha256_file

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OWNERS = {"verify": "Agent 3", "market": "Agent 6", "core.proforma": "Agent 4", "evalq.score": "Agent 4"}


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise SystemExit(f"module {name} is missing ({OWNERS.get(name, 'unknown owner')}): {exc}")


def doc_ids(spec: str) -> list[str]:
    if spec.isdigit():
        src = DATA / "labels" / "demo_corpus.txt"
        if not src.exists():
            src = DATA / "corpus.txt"
        lines = src.read_text().split()
        return [ln.split("_")[0] for ln in lines][: int(spec)]
    return [ln.split("_")[0] for ln in Path(spec).read_text().split()]


def pdf_path(doc_id: str) -> Path:
    matches = sorted((DATA / "pdfs").glob(f"{doc_id}_*.pdf"))
    if not matches:
        raise FileNotFoundError(f"no PDF for {doc_id} in data/pdfs")
    return matches[0]


def load_facts(extractor: str, doc_id: str) -> list[Fact]:
    path = DATA / "facts" / extractor / f"{doc_id}.json"
    return [Fact(**f) for f in json.loads(path.read_text())] if path.exists() else []


def runs_for(facts: list[Fact], doc_id: str) -> list[ExtractionRun]:
    known: dict[str, ExtractionRun] = {}
    for path in (DATA / "runs").glob("*.json"):
        data = json.loads(path.read_text())
        for r in data if isinstance(data, list) else [data.get("run", data)]:
            if isinstance(r, dict) and "id" in r:
                known[r["id"]] = ExtractionRun(**r)
    out: dict[str, ExtractionRun] = {}
    for f in facts:
        if f.run_id and f.run_id not in out:
            model, _, prompt = (f.model_version or f.author).partition("@")
            out[f.run_id] = known.get(f.run_id) or ExtractionRun(
                id=f.run_id, extractor=f.extractor, model=model, prompt_version=prompt or "unknown",
                started_at=f.created_at, doc_ids=[doc_id])
    return list(out.values())


def run_doc(doc_id: str, extractors: list[str]) -> dict:
    verify = _module("verify")
    market = _module("market")
    proforma = _module("core.proforma")
    score = _module("evalq.score")

    path = pdf_path(doc_id)
    store.insert_document(Document(id=doc_id, filename=path.name, sha256=sha256_file(path),
                                   page_count=page_count(path)))

    raw = {e: load_facts(e, doc_id) for e in extractors}
    raw = {e: fs for e, fs in raw.items() if fs}
    if not raw:
        return {"doc_id": doc_id, "status": "no facts"}

    if not store.current_facts(doc_id=doc_id):
        store.insert_runs(runs_for([f for fs in raw.values() for f in fs], doc_id))
        routed = verify.verify_doc(raw, page_texts(path), pdf_path=path)
        store.insert_facts(routed)
    if not store.labels(doc_id=doc_id):
        label_path = DATA / "labels" / f"{doc_id}.json"
        if label_path.exists():
            store.insert_labels([Label(**lb) for lb in json.loads(label_path.read_text())])

    facts = store.current_facts(doc_id=doc_id)
    ptype = next((f.value for f in facts if f.field == "property_type" and f.value), None)
    exit_yield = market.get_exit_yield(doc_id, ptype)
    version = proforma.build(doc_id, author="pipeline", reason="initial build", exit_yield=exit_yield)

    eval_run_id = new_id()
    verdicts: dict[str, int] = {}
    for extractor in raw:
        rows = score.score_doc(doc_id, extractor, eval_run_id)
        store.insert_eval(rows)
        for r in rows:
            verdicts[f"{extractor}:{r.verdict}"] = verdicts.get(f"{extractor}:{r.verdict}", 0) + 1

    review = sum(1 for f in facts if f.review_status == "review")
    return {"doc_id": doc_id, "status": "ok", "facts": len(facts), "review": review,
            "version": version.version_no, "exit_yield": exit_yield.get("value"),
            "yield_provider": exit_yield.get("provider"), "verdicts": verdicts}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", required=True, help="file with doc ids / filenames, or a number N")
    ap.add_argument("--extractors", default="v7go,grok")
    args = ap.parse_args()

    store.apply_schema()
    extractors = [e.strip() for e in args.extractors.split(",") if e.strip()]
    for doc_id in doc_ids(args.docs):
        try:
            print(json.dumps(run_doc(doc_id, extractors)))
        except SystemExit:
            raise
        except Exception as exc:
            print(json.dumps({"doc_id": doc_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"}))


if __name__ == "__main__":
    main()
