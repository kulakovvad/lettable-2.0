"""Apply db/schema.sql and load the Hidings fixtures. Usage: python -m scripts.load_fixtures [--reset]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import store
from core.models import Document, ExtractionRun, Fact, Label

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
    for extractor, model in (("v7go", "v7go"), ("grok", "grok-4")):
        facts = fixture_facts(extractor)
        run = ExtractionRun(id=facts[0].run_id, extractor=extractor, model=model,
                            prompt_version="fixture", doc_ids=[doc.id])
        store.start_run(run)
        store.insert_facts(facts)
        store.finish_run(run.id, tokens=0, cost=0.0)
    store.insert_labels(fixture_labels())
    print(f"loaded {doc.id}: {len(store.current_facts(doc_id=doc.id))} facts, "
          f"{len(store.labels(doc_id=doc.id))} labels")


if __name__ == "__main__":
    main()
