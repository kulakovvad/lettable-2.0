"""Export evaluation metrics from the database.

Usage: python -m evalq.export [--md] [--out data/metrics.json]
Writes the summary JSON; with --md prints markdown tables for the README.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.models import EvalRow
from evalq.score import summary

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "metrics.json"


def latest_rows(rows: list[EvalRow]) -> list[EvalRow]:
    """Keep, for each (extractor, doc), only rows of its most recent eval run."""
    latest: dict[tuple[str, str], tuple[Any, str]] = {}
    for r in rows:
        key = (r.extractor, r.doc_id)
        if key not in latest or r.run_at > latest[key][0]:
            latest[key] = (r.run_at, r.eval_run_id)
    return [r for r in rows if latest[(r.extractor, r.doc_id)][1] == r.eval_run_id]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def to_markdown(s: dict[str, Any]) -> str:
    lines: list[str] = []
    header = ("| Labels | Extractor | Docs | Units | Labelled fields | Caught | Missed | Wrong "
              "| Invented | Ungrounded | Precision | Recall | Auto share | Auto precision |")
    lines += [header, "|" + " --- |" * 14]
    for scope in ("agreed", "all"):
        block = s.get(scope) or {}
        for ext, b in block.items():
            o, sm, auto = b["overall"], b["sample"], b["by_review_status"]["auto"]
            lines.append(
                f"| {scope} | {ext} | {sm['documents']} | {sm['units']} | {sm['labelled_fields']} "
                f"| {o['caught']} | {o['missed']} | {o['wrong']} | {o['invented']} "
                f"| {o['ungrounded']} | {_pct(o['precision'])} | {_pct(o['recall'])} "
                f"| {_pct(b['auto_share'])} | {_pct(auto['precision'])} (n={auto['n']}) |")
    lines += ["", "Per field (all labels):", "",
              "| Field | " + " | ".join(f"{e} P / R (n)" for e in s["all"]) + " |",
              "|" + " --- |" * (1 + len(s["all"]))]
    fields: list[str] = []
    for b in s["all"].values():
        fields += [f for f in b["by_field"] if f not in fields]
    for f in fields:
        cells = []
        for b in s["all"].values():
            st = b["by_field"].get(f)
            cells.append("-" if st is None else
                         f"{_pct(st['precision'])} / {_pct(st['recall'])} ({st['n']})")
        lines.append(f"| {f} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    from core import store

    parser = argparse.ArgumentParser()
    parser.add_argument("--md", action="store_true", help="print markdown tables")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id", default=None, help="only this eval_run_id")
    args = parser.parse_args()

    rows = store.eval_rows(eval_run_id=args.run_id)
    if args.run_id is None:
        rows = latest_rows(rows)
    s = summary(rows, store.labels())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(s, indent=2, default=str))
    if args.md:
        print(to_markdown(s))
    else:
        print(json.dumps(s, indent=2, default=str))


if __name__ == "__main__":
    main()
