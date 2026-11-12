"""Build reference labels for the corpus.

    python -m reference.build_labels --all [--store]
    python -m reference.build_labels data/pdfs/acuitus-90f42467_....pdf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.models import Label
from reference import acuitus_parser, selfcheck

PDF_DIR = Path("data/pdfs")
CORPUS = Path("data/corpus.txt")
OUT_DIR = Path("data/labels")
DEMO_SIZE = 15


def corpus_paths() -> list[Path]:
    names = [ln.strip() for ln in CORPUS.read_text().splitlines() if ln.strip()]
    return [PDF_DIR / n for n in names]


def build_one(path: Path) -> tuple[list[Label], dict]:
    doc_id = acuitus_parser.doc_id_from_path(path)
    try:
        labels = acuitus_parser.parse(path, doc_id)
        error = None
    except Exception as exc:  # one broken brochure must not stop the corpus run
        labels, error = [], f"parser error: {type(exc).__name__}: {exc}"
    status, why = selfcheck.check_status(labels, doc_id)
    if error:
        status, why = "disputed", [error]
    labels = [lb.model_copy(update={"status": status}) for lb in labels]
    units = sorted({lb.unit_id for lb in labels if lb.unit_id is not None})
    stepped = any(lb.field == "passing_rent" and "rising" in (lb.quote or "") for lb in labels)
    row = {
        "doc_id": doc_id,
        "file": path.name,
        "units": len(units),
        "labels": len(labels),
        "breaks": sum(lb.field == "break_date" for lb in labels),
        "stepped_rent": stepped,
        "status": status,
        "reasons": why,
        "warnings": selfcheck.listing_warnings(doc_id, labels),
    }
    return labels, row


def demo_corpus(report: list[dict], size: int = DEMO_SIZE) -> list[str]:
    agreed = [r for r in report if r["status"] == "agreed"]

    def score(r: dict) -> tuple:
        return (r["units"] >= 3, r["breaks"] > 0, r["stepped_rent"], r["units"], r["labels"])

    return [r["doc_id"] for r in sorted(agreed, key=score, reverse=True)[:size]]


def run(paths: list[Path], store: bool = False) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    all_labels: list[Label] = []
    for path in paths:
        labels, row = build_one(path)
        (OUT_DIR / f"{row['doc_id']}.json").write_text(
            json.dumps([lb.model_dump() for lb in labels], indent=1, ensure_ascii=False))
        report.append(row)
        all_labels += labels
        print(f"{row['doc_id']}: {row['status']} units={row['units']} "
              f"labels={row['labels']} {'; '.join(row['reasons'])}")
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    agreed = sum(r["status"] == "agreed" for r in report)
    print(f"agreed {agreed} of {len(report)}")
    if len(paths) > 1:
        demo = demo_corpus(report)
        (OUT_DIR / "demo_corpus.txt").write_text("\n".join(demo) + "\n")
        print(f"demo corpus: {len(demo)} docs")
    if store:
        from core import store as db
        db.insert_labels(all_labels)
        print(f"stored {len(all_labels)} labels")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--all", action="store_true", help="every file in data/corpus.txt")
    ap.add_argument("--store", action="store_true", help="also insert labels into the database")
    args = ap.parse_args()
    paths = corpus_paths() if args.all else args.paths
    if not paths:
        ap.error("give PDF paths or --all")
    run(paths, store=args.store)


if __name__ == "__main__":
    main()
