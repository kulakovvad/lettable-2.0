# CONTRACTS

Frozen after phase 0. Source of truth for types: `core/models.py`. Rules: `AGENTS.md`. Need a change? Ask the integrator.

## Setup

```bash
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env          # local default DATABASE_URL points at Docker container tracelease-pg
.venv/bin/python -m scripts.load_fixtures --reset   # applies db/schema.sql, loads Hidings fixtures
.venv/bin/pytest -q
```

## Fields

Unit-level (`unit_id = "Unit 1"` etc., one row of the tenancy schedule): `tenant`, `tenant_crn` (8-char string), `area_sqft` (int), `passing_rent` (int GBP p.a.x. at valuation date), `lease_start`, `lease_term_years` (float), `lease_expiry`, `break_date` (first tenant break), `rent_review_date` (next review). Property-level (`unit_id = null`): `address`, `property_type`, `total_rent` (int), `total_area_sqft` (int), `niy_pct` (float). Dates are ISO `yyyy-mm-dd`. Constants: `UNIT_FIELDS`, `PROPERTY_FIELDS`, `FIELD_TYPES`, `FIELD_UNITS`, `DATE_FIELDS`.

## Fact JSON

```json
{"id": "v7go-unit2-passing_rent", "doc_id": "acuitus-90f42467", "unit_id": "Unit 2", "field": "passing_rent",
 "value": 22300, "unit": "GBP/yr", "page": 3, "quote": "£22,300", "bbox": null,
 "grounded": null, "date_check": null, "confidence": 0.9, "review_status": null, "review_reason": null,
 "run_id": "run-fixture-v7go", "extractor": "v7go", "model_version": "v7go@fixture", "author": "v7go@fixture",
 "created_at": "2026-11-12T21:00:00Z", "supersedes_id": null, "reason": null, "snapshot_id": null}
```

IDs are text (`uuid4().hex` by default), so pure functions can reference facts without the DB. Pages are 1-based. `extractor`: `v7go | grok | grok-b | manual | grok-bot | rca | fallback`. `review_reason`: `extractors_disagree | ungrounded | date_inconsistent | number_not_in_quote | missing_in_one`. `verdict`: `caught | missed | wrong | invented | ungrounded`. Other models: `Document`, `Label`, `Cell`, `ProformaVersion`, `Snapshot`, `ExtractionRun`, `EvalRow`.

## Fixtures (`fixtures/`, doc `acuitus-90f42467`, The Hidings, Cirencester)

`pages_hidings.json` (text of pages 1-3, sha256), `labels.json` (36 agreed parser labels; units sum to £110,800), `facts_v7.json`, `facts_grok.json`. Planted errors: V7 Unit 3 `passing_rent = 25500` (stepped future rent; Grok says 20000), Grok misses Unit 1 `break_date` (footnote 2, 01/06/2027), Grok invents Unit 2 `break_date` with a quote not on page 3. Natural trap: Unit 2 "7 years from 29/09/2026" implies 28/09/2033 but the reversion prints 31/08/2033 (date_inconsistent). Brochures: `data/pdfs/` (symlink), list in `data/corpus.txt`.

## Public functions

| Module (owner) | Signatures |
| --- | --- |
| `core/store.py` (0) | `apply_schema()`, `insert_document(Document) -> str`, `documents()`, `get_document(id)`, `start_run(ExtractionRun) -> str`, `finish_run(run_id, tokens, cost)`, `extraction_runs(extractor=None)`, `insert_facts(list[Fact]) -> list[str]`, `current_facts(doc_id=None, extractor=None) -> list[Fact]`, `get_fact(id)`, `fact_history(fact_id) -> list[Fact]`, `insert_labels(list[Label])`, `labels(doc_id=None, status=None)`, `new_version(ProformaVersion) -> str`, `versions(doc_id)`, `get_version(id)`, `insert_cells(list[Cell])`, `cells(version_id)`, `get_cell(id)`, `insert_snapshot(Snapshot) -> str`, `get_snapshot(id)`, `insert_eval(list[EvalRow])`, `eval_rows(eval_run_id=None, extractor=None)` |
| `core/pdf.py` (0) | `page_texts(path) -> list[str]`, `find_bbox(path, page, quote) -> list[float] or None`, `render_page(path, page, bbox=None, zoom=2.0) -> bytes`, `sha256_file(path)`, `page_count(path)` |
| `reference/` (1) | `acuitus_parser.parse(path, doc_id) -> list[Label]`, `selfcheck.check(doc_id, labels) -> list[Label]` (sets status), `arbiter.arbitrate(path, doc_id, disputed: list[Label]) -> list[Label]` |
| `extract/` (2) | `grok.extract(path, doc_id, run_id) -> list[Fact]`, `v7go.extract(path, doc_id, run_id) -> list[Fact]`, `runner.extract(doc_path, extractor) -> list[Fact]` (writes `extraction_runs` + facts, reads/writes `data/raw/<extractor>/<doc_id>.json`) |
| `verify/` (3) | `grounding.ground(fact, page_text, pdf_path=None) -> Fact`, `dates.check_unit(facts) -> dict[str, str]`, `routing.route(facts_v7, facts_grok) -> list[Fact]` (sets `review_status`, `review_reason`) |
| `core/proforma.py` (4) | `build(facts, assumptions: dict, version_id) -> list[Cell]` (assumptions: `valuation_date`, `exit_yield`) |
| `core/explain.py` (4) | `explain(cell_id) -> dict` (cell, formula, inputs, facts with doc/page/quote/bbox or snapshot) |
| `core/audit.py` (4) | `correct_fact(fact_id, value, reason, author) -> Fact` (empty reason raises), `recompute(doc_id, trigger_fact_id, author, reason) -> ProformaVersion`, `diff(v1_id, v2_id) -> list[dict]`, `history(fact_id) -> list[Fact]` |
| `evalq/score.py` (4) | `score(facts, labels, eval_run_id) -> list[EvalRow]`, `summary(rows) -> dict` |
| `app/streamlit_app.py` (5) | UI only; reads through `core.store`, `core.*`, `evalq` |
| `market/` (6) | `rca.exit_yield(property_type) -> Fact` (writes `Snapshot`), `fallback.exit_yield(doc_id) -> Fact` |
| `mcp_server/server.py` (6) | tools `explain_figure`, `get_fact_history`, `eval_summary`, `review_queue`, `diff_versions`, `correct_fact` (author `grok-bot:<name>`) |
| `scripts/` (0) | `load_fixtures.py [--reset]`, `run_pipeline.py --docs N` (checkpoint 1) |

## Database

`db/schema.sql`: `documents`, `extraction_runs`, `snapshots`, `facts`, `proforma_versions`, `cells`, `labels`, `eval_runs` + view `current_facts` (heads of `supersedes_id` chains). Triggers reject `UPDATE`/`DELETE` on everything except `extraction_runs`. A fact with `supersedes_id` and no `reason` violates a check constraint.

## Ownership

0 Integrator: `core/models.py`, `core/store.py`, `core/pdf.py`, `db/`, `scripts/`, `fixtures/`, root config. 1: `reference/`. 2: `extract/`. 3: `verify/`. 4: `core/proforma.py`, `core/explain.py`, `core/audit.py`, `evalq/`. 5: `app/`. 6: `market/`, `mcp_server/`. 7: `README.md`, `docs/`. Tests: `tests/test_<area>_*.py` per owner.
