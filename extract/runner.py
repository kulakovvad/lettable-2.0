"""Batch extraction CLI. Writes data/facts/<extractor>/<doc_id>.json and data/runs/<extractor>.json.

python -m extract.runner --docs data/corpus.txt --extractors v7go,grok [--workers 8] [--limit N]

Does not touch the database: grounding and insertion are the integrator pipeline's job.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from core.models import ExtractionRun, Fact, utcnow
from extract.common import doc_id_for

PDF_DIR = Path("data/pdfs")
FACTS_DIR = Path("data/facts")
RUNS_DIR = Path("data/runs")

Extract = Callable[[Path], tuple[list[Fact], ExtractionRun]]


def _extractor(name: str) -> Extract:
    if name == "grok":
        from extract.grok import extract_grok
        return lambda p: extract_grok(p, "a")
    if name == "grok-b":
        from extract.grok import extract_grok
        return lambda p: extract_grok(p, "b")
    if name == "v7go":
        from extract.v7go import extract_v7
        return extract_v7
    raise ValueError(f"unknown extractor {name!r}")


def read_corpus(docs: str | Path, limit: int | None = None) -> list[Path]:
    """Corpus file lines are PDF file names (or paths); blank lines and '#' comments are skipped."""
    out: list[Path] = []
    for line in Path(docs).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        out.append(p if p.exists() else PDF_DIR / p.name)
    return out[:limit] if limit else out


def write_facts(extractor: str, doc_id: str, facts: list[Fact]) -> Path:
    p = FACTS_DIR / extractor / f"{doc_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([f.model_dump(mode="json") for f in facts], indent=1, ensure_ascii=False))
    return p


def merge_runs(extractor: str, runs: list[ExtractionRun]) -> ExtractionRun | None:
    """One ExtractionRun per extractor per batch: tokens and cost summed, doc_ids concatenated."""
    if not runs:
        return None
    tokens = [r.tokens for r in runs if r.tokens is not None]
    costs = [r.cost for r in runs if r.cost is not None]
    return ExtractionRun(
        extractor=extractor,
        model=runs[0].model,
        prompt_version=runs[0].prompt_version,
        started_at=min(r.started_at for r in runs),
        finished_at=utcnow(),
        tokens=sum(tokens) if tokens else None,
        cost=round(sum(costs), 6) if costs else None,
        doc_ids=[d for r in runs for d in r.doc_ids],
    )


def run_extractor(extractor: str, paths: list[Path], workers: int = 8,
                  fn: Extract | None = None) -> tuple[ExtractionRun | None, dict[str, str]]:
    """Extract every doc; one failure never stops the batch. Returns (merged run, {doc_id: error})."""
    fn = fn or _extractor(extractor)
    runs: list[ExtractionRun] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, p): p for p in paths}
        for fut in as_completed(futures):
            doc_id = doc_id_for(futures[fut])
            try:
                facts, run = fut.result()
            except Exception as exc:  # noqa: BLE001 - per-document isolation
                errors[doc_id] = f"{type(exc).__name__}: {exc}"
                print(f"[{extractor}] {doc_id} FAILED {errors[doc_id]}", file=sys.stderr)
                continue
            # Facts carry the per-doc run id; the merged run below records batch totals.
            write_facts(extractor, doc_id, facts)
            runs.append(run)
            print(f"[{extractor}] {doc_id} facts={len(facts)} tokens={run.tokens}", flush=True)
    merged = merge_runs(extractor, runs)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run": merged.model_dump(mode="json") if merged else None,
        "per_doc": [r.model_dump(mode="json") for r in runs],
        "errors": errors,
    }
    (RUNS_DIR / f"{extractor}.json").write_text(json.dumps(payload, indent=1))
    return merged, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", default="data/corpus.txt")
    ap.add_argument("--extractors", default="v7go,grok")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    paths = read_corpus(args.docs, args.limit)
    for name in [e.strip() for e in args.extractors.split(",") if e.strip()]:
        try:
            merged, errors = run_extractor(name, paths, args.workers)
        except Exception:  # noqa: BLE001 - an extractor that cannot start must not stop the others
            traceback.print_exc()
            continue
        n = len(merged.doc_ids) if merged else 0
        print(f"[{name}] done docs={n} failed={len(errors)} tokens={merged.tokens if merged else None} "
              f"cost={merged.cost if merged else None}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
