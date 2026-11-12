from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.models import EvalRow, Fact
from evalq.export import latest_rows, to_markdown
from evalq.score import latest_run_facts, score, stats, summary
from tests.test_core_support import DOC, load_labels, routed


def verdicts(rows: list[EvalRow]) -> dict[tuple[str | None, str], str]:
    return {(r.unit_id, r.field): r.verdict for r in rows}


def test_score_fixtures_finds_planted_errors() -> None:
    labels = load_labels()
    v7 = score(routed("v7go"), labels, "v7go", "run1")
    grok = score(routed("grok"), labels, "grok", "run1")
    assert verdicts(v7)[("Unit 3", "passing_rent")] == "wrong"
    assert verdicts(grok)[("Unit 1", "break_date")] == "missed"
    assert verdicts(grok)[("Unit 2", "break_date")] == "ungrounded"
    assert verdicts(grok)[("Unit 3", "passing_rent")] == "caught"
    all_v = {r.verdict for r in v7 + grok}
    assert {"missed", "wrong", "ungrounded", "caught"} <= all_v
    assert all(r.eval_run_id == "run1" for r in v7 + grok)


def test_unlabelled_fields_are_not_scored_and_extra_facts_are_invented() -> None:
    labels = load_labels()
    facts = routed("v7go")
    extra = Fact(doc_id=DOC, field="niy_pct", value=7.5, page=2, quote="7.5%", grounded=True,
                 review_status="auto", extractor="v7go", author="v7go@fixture")
    rows = score(facts + [extra], labels, "v7go", "run1")
    fields = {r.field for r in rows}
    assert "address" not in fields and "property_type" not in fields
    assert verdicts(rows)[(None, "niy_pct")] == "invented"


def test_docs_without_labels_are_skipped() -> None:
    other = Fact(doc_id="acuitus-other", unit_id="Unit 1", field="passing_rent", value=1,
                 page=1, quote="£1", extractor="v7go", author="v7go@fixture")
    rows = score([other], load_labels(), "v7go")
    assert all(r.doc_id != "acuitus-other" for r in rows)


def test_review_status_is_carried_into_rows() -> None:
    rows = score(routed("v7go"), load_labels(), "v7go", "run1")
    r = next(r for r in rows if (r.unit_id, r.field) == ("Unit 3", "passing_rent"))
    assert r.review_status == "review"


def test_stats_precision_recall() -> None:
    mk = lambda v: EvalRow(eval_run_id="r", extractor="x", doc_id=DOC, field="f", verdict=v)
    s = stats([mk("caught")] * 6 + [mk("wrong"), mk("missed"), mk("invented"), mk("ungrounded")])
    assert s["precision"] == round(6 / 9, 4)
    assert s["recall"] == round(6 / 8, 4)
    assert stats([])["precision"] is None


def test_summary_splits_extractor_field_routing_and_agreed() -> None:
    labels = load_labels()
    rows = score(routed("v7go"), labels, "v7go", "r") + score(routed("grok"), labels, "grok", "r")
    disputed = [lb.model_copy(update={"status": "disputed"}) if lb.id == "lbl-unit3-passing_rent"
                else lb for lb in labels]
    s = summary(rows, disputed)
    v7 = s["all"]["v7go"]
    assert v7["overall"]["wrong"] == 1
    assert v7["by_field"]["passing_rent"]["wrong"] == 1
    assert v7["sample"]["documents"] == 1 and v7["sample"]["units"] == 4
    assert v7["by_review_status"]["auto"]["precision"] == 1.0
    assert 0 < v7["auto_share"] < 1
    assert s["agreed"]["v7go"]["overall"]["wrong"] == 0
    assert summary(rows)["agreed"] is None
    md = to_markdown(s)
    assert "| agreed | v7go |" in md and "passing_rent" in md


def test_latest_run_facts_drops_older_extraction_runs() -> None:
    old = routed("grok")
    later = datetime(2026, 11, 12, 22, tzinfo=timezone.utc)
    rerun = [f.model_copy(update={"id": "rerun-" + f.id, "run_id": "run-2", "created_at": later})
             for f in old]
    kept = latest_run_facts(old + rerun)
    assert {f.run_id for f in kept} == {"run-2"} and len(kept) == len(old)
    rows = score(kept, load_labels(), "grok", "r")
    assert not any(r.verdict == "invented" for r in rows)


def test_latest_rows_keeps_last_run_per_extractor_and_doc() -> None:
    t0 = datetime(2026, 11, 12, 21, tzinfo=timezone.utc)
    old = EvalRow(eval_run_id="a", extractor="grok", doc_id=DOC, field="f", verdict="missed",
                  run_at=t0)
    new = EvalRow(eval_run_id="b", extractor="grok", doc_id=DOC, field="f", verdict="caught",
                  run_at=t0 + timedelta(minutes=5))
    assert [r.eval_run_id for r in latest_rows([old, new])] == ["b"]
