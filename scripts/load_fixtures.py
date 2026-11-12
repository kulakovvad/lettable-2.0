"""Apply db/schema.sql and load the Hidings fixtures. Usage: python -m scripts.load_fixtures [--reset]

Facts go through verify_doc before they are stored, then both extractors are scored and
proforma v1 is built, so the UI has the same state the pipeline would produce.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import store
from core.models import Document, ExtractionRun, Fact, Label, new_id
from core.proforma import build
from evalq.score import score_doc
from verify import verify_doc

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def fixture_document() -> Document:
    pages = load_json("pages_hidings.json")
    return Document(id=pages["doc_id"], filename=pages["filename"], sha256=pages["sha256"],
                    source="acuitus", page_count=pages["page_count"])


def fixture_facts(extractor: str) -> list[Fact]:
    name = {"v7go": "facts_v7.json", "grok": "facts_grok.json"}[extractor]
    return [Fact(**f) for f in load_json(name)]


def fixture_labels() -> list[Label]:
    return [Label(**lb) for lb in load_json("labels.json")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop all tables first (dev only)")
    args = parser.parse_args()

    if args.reset:
        store.reset_schema()
    else:
        store.apply_schema()

    doc = fixture_document()
    if store.current_facts(doc_id=doc.id):
        print(f"fixtures already loaded for {doc.id}; use --reset to reload")
        return

    store.insert_document(doc)
    raw: dict[str, list[Fact]] = {}
    for extractor, model in (("v7go", "v7go"), ("grok", "grok-4")):
        raw[extractor] = fixture_facts(extractor)
        store.start_run(ExtractionRun(id=raw[extractor][0].run_id, extractor=extractor,
                                      model=model, prompt_version="fixture", doc_ids=[doc.id]))
    pages = [p["text"] for p in load_json("pages_hidings.json")["pages"]]
    routed = verify_doc(raw, pages)
    store.insert_facts(routed)
    for facts in raw.values():
        store.finish_run(facts[0].run_id, tokens=0, cost=0.0)
    store.insert_labels(fixture_labels())

    eval_run_id = new_id()
    for extractor in raw:
        store.insert_eval(score_doc(doc.id, extractor, eval_run_id))
    version = build(doc.id, author="pipeline", reason="initial build")

    review = sum(1 for f in routed if f.review_status == "review")
    print(f"loaded {doc.id}: {len(routed)} verified facts ({review} in review), "
          f"{len(store.labels(doc_id=doc.id))} labels, eval run {eval_run_id}, "
          f"proforma v{version.version_no}")


if __name__ == "__main__":
    main()
