import re

import pytest

from core.pdf import page_texts, sha256_file
from market import fallback


def test_benchmark_by_sector():
    assert fallback.benchmark_yield("Industrial")["value"] == 5.6
    b = fallback.benchmark_yield("Retail")
    assert b["value"] == 6.1 and b["page"] == 2 and b["author"] == "analyst-assumption"
    assert fallback.benchmark_yield(None)["key"] == "british_land_hy27"


def test_quote_contains_value():
    for b in fallback.BENCHMARKS.values():
        assert str(b["value"]) in b["quote"]


def test_benchmark_fact_and_snapshot():
    b = fallback.benchmark_yield("Office")
    snap = fallback.benchmark_snapshot(b)
    f = fallback.benchmark_fact("doc-1", b, snapshot_id=snap.id)
    assert snap.provider == "fallback" and snap.row_count == 1
    assert snap.id == fallback.benchmark_snapshot(b).id
    assert f.extractor == "fallback" and f.field == "exit_yield" and f.unit == "pct"
    assert f.quote and f.page and f.reason and f.snapshot_id == snap.id
    assert fallback.exit_yield("doc-1", "Office").value == 6.1


@pytest.mark.parametrize("key", list(fallback.BENCHMARKS))
def test_quote_is_verbatim_in_source_pdf(key):
    path = fallback.source_pdf(key)
    if not path.exists():
        pytest.skip("Source/ not present")
    b = fallback.BENCHMARKS[key]
    assert sha256_file(path) == b["sha256"]
    text = re.sub(r"\s+", " ", page_texts(path)[b["page"] - 1])
    assert b["quote"] in text
