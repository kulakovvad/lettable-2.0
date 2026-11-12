import json
from datetime import date

import pytest

import market
from market import fallback, rca
from market.types import sector


def test_sector_mapping():
    assert sector("Retail, High Street Retail") == "retail"
    assert sector("Industrial, Ground Rent") == "industrial"
    assert sector("Tyre and Exhaust Depot") == "industrial"
    assert sector("Retail and Office") == "mixed"
    assert sector("Hotel") == "leisure"
    assert sector("Assisted Living") is None
    assert sector(None) is None


def test_maintype():
    assert [rca.maintype(s) for s in ("industrial", "retail", "office", "leisure", "mixed", None)] == [
        "Industrial", "Retail", "Office", "Hotel", "Other", "Other"]


def test_render_query_filters_active_and_substitutes_params():
    sql = rca.render_query("Retail", date(2026, 11, 12))
    assert "ACTIVE_FG = TRUE" in sql
    assert "MAINTYPE = 'Retail'" in sql
    assert "'2026-11-12'::date" in sql
    assert "%(" not in sql


def test_median_pct_and_hash_are_order_independent():
    assert rca.median_pct([0.07, 0.06, 0.065]) == 6.5
    rows = [("1", "p1", 0.06), ("2", "p2", 0.07)]
    assert rca.result_hash(rows) == rca.result_hash(list(reversed(rows)))
    with pytest.raises(ValueError):
        rca.median_pct([])


def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rca, "RAW_DIR", tmp_path)
    cache = {"maintype": "Retail", "valuation_date": "2026-11-12",
             "query_sql": rca.render_query("Retail", date(2026, 11, 12)),
             "executed_at": "2026-11-12T22:00:00+00:00", "snapshot_id": "snap-test",
             "rows": [["d1", "p1", 0.07], ["d2", "p2", 0.08], ["d3", "p3", 0.06]]}
    (tmp_path / "Retail.json").write_text(json.dumps(cache))


def test_rca_exit_yield_reads_cache_without_network(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch)
    monkeypatch.setattr(rca, "_fetch", lambda *a: pytest.fail("live query on cached rerun"))
    value, snap = rca.rca_exit_yield("retail")
    assert value == 7.0
    assert snap.id == "snap-test" and snap.provider == "rca"
    assert snap.row_count == 3 and snap.row_ids == ["d1", "d2", "d3"]


def test_get_exit_yield_prefers_rca(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch)
    monkeypatch.setattr(market, "_persist", lambda fact, snap: (snap.id, fact.id))
    ey = market.get_exit_yield("doc-1", "Retail Parade")
    assert ey["provider"] == "rca" and ey["value"] == 7.0
    assert ey["author"] == "rca@snap-test" and ey["snapshot_id"] == "snap-test"
    assert ey["sample_size"] == 3 and ey["source_fact_id"]


def test_get_exit_yield_falls_back_to_benchmark(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no RCA")

    monkeypatch.setattr(rca, "rca_exit_yield", boom)
    monkeypatch.setattr(market, "_persist", lambda fact, snap: (snap.id, fact.id))
    ey = market.get_exit_yield("doc-1", "Industrial, Warehouse")
    assert ey["provider"] == "fallback" and ey["value"] == 5.6
    assert ey["author"] == fallback.AUTHOR and ey["page"] == 4
    assert "not MSCI RCA" in ey["reason"]
