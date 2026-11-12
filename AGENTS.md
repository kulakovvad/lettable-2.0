# AGENTS.md — mandatory rules for every agent

Read this file fully before writing any code. If a rule here conflicts with a prompt, this file wins; ask the integrator.

TraceLease turns UK commercial tenancy schedules (sale particulars and leases) into a proforma where every number traces back to a quote, a page, an author and an edit history, plus an honest evaluation of what the AI caught, missed, got wrong or invented. Hackathon build: code freeze at 22:40, submission at 23:00.

## 1. Language: English only

- All code, identifiers, comments, docstrings, log messages, UI strings, commit messages, README and docs are in English.
- No Cyrillic text may reach GitHub. The pre-commit hook rejects it; do not bypass it with `--no-verify`.
- `Plans/` and `Source/` are git-ignored and may contain Russian; never copy text from them into tracked files without translating it.

## 2. Ownership and coordination

Each agent writes only inside its own directories. Touching another agent's files = ask the integrator.

| Agent | Owns |
| --- | --- |
| 0 — Integrator | `core/models.py`, `core/store.py`, `core/pdf.py`, `db/`, `scripts/`, `fixtures/`, `requirements.txt`, `.gitignore`, `CONTRACTS.md`, `AGENTS.md` |
| 1 — Reference labels | `reference/`, `tests/test_reference_*.py` |
| 2 — Extraction | `extract/`, `tests/test_extract_*.py` |
| 3 — Verification | `verify/`, `tests/test_verify_*.py` |
| 4 — Core logic and eval | `core/proforma.py`, `core/explain.py`, `core/audit.py`, `evalq/`, `tests/test_core_*.py`, `tests/test_eval_*.py` |
| 5 — UI | `app/` |
| 6 — Market and MCP | `market/`, `mcp_server/`, `tests/test_market_*.py`, `tests/test_mcp_*.py` |
| 7 — Docs | `README.md`, `docs/` |

- `core/models.py` and `CONTRACTS.md` are frozen after phase 0. Need a new field? Ask the integrator; do not add it locally.
- Do not run `git add`, `commit`, `push`, `checkout`, `stash` or `reset`. Only the integrator commits.
- Do not add dependencies. If you need a package, ask the integrator to add it to `requirements.txt`.
- Build against `fixtures/` first. Never block on another agent's module; stub it behind the signature in `CONTRACTS.md`.
- When done, report back in this format: files changed, how to run, what is mocked, known gaps.

## 3. Trust rules (the product depends on these)

- A machine-extracted fact without a verbatim `quote` and a `page` never enters the proforma. Human facts (manual edits, review confirmations) enter without a quote but must carry an author and a `reason`.
- The database is append-only. Never `UPDATE` or `DELETE` facts, cells or labels; a correction is a new row with `supersedes_id`.
- A manual correction without a `reason` is rejected.
- Every fact records its author: `model@version` (with `run_id`) for extraction, a person's name for manual edits, `grok-bot:<name>` for MCP edits, `rca@<snapshot_id>` for market data.
- Every number shown in the UI, README or video is computed from the database. Never hardcode metrics, never round in a flattering direction, always show sample size.
- Vendor citations (V7 Go) are claims, not proof: they go through the same grounding check as Grok output.
- Do not claim prompt-injection protection. Grounding stops invented values, not injected ones.

## 4. Domain conventions (UK commercial property)

- Dates are UK format `dd/mm/yyyy`. Always parse with `dayfirst=True`; store ISO `yyyy-mm-dd`. Never guess US order.
- "N years from DD/MM/YYYY" means expiry = start + N years − 1 day. Acuitus prints the expiry (reversion) in brackets, e.g. `(31/05/2032)`; check against it with ±1 day tolerance.
- Tenant breaks are often only in footnotes ("option to determine the lease on …"). Always scan footnotes.
- Rent is annual, GBP, exclusive of VAT (`p.a.x.`). Store as integer pounds. Stepped rent ("£20,000 rising to £25,500 in April 2027"): passing rent = the amount at the valuation date.
- Area in sq ft (integer). Acuitus shows sq m with sq ft in brackets; take the sq ft value, do not convert.
- CRN (Companies House number) is an 8-character string; keep leading zeros (`00526923`), never cast to int.
- WAULT is rent-weighted, measured in years from the valuation date (`VALUATION_DATE`, default `2026-11-12`). Breaks already passed are ignored in WAULT to break.
- Value = total passing rent / exit yield. This is a simple gross value; say so in the UI, do not deduct purchaser's costs silently.
- Unit of extraction is one row of the tenancy schedule (`unit_id`). Property-level fields use `unit_id = null`.

## 5. Code style

- Python 3.13, type hints everywhere, `pydantic` models from `core/models.py` at module boundaries.
- Prefer small pure functions. `verify/`, `evalq/` and `core/proforma.py` must not touch the network or the database.
- Network calls only in `extract/`, `reference/arbiter.py`, `market/` and `mcp_server/`.
- Cache every model response to `data/raw/<extractor>/<doc_id>.json` and read the cache on rerun. The demo video is recorded from cached runs, with no live model calls.
- Database access only through `core/store.py`. Connection string comes from `DATABASE_URL`.
- Write a `pytest` test for every public function, using `fixtures/`. Keep tests fast and offline.
- Comments explain constraints the code cannot show. No narration comments.

## 6. Secrets and data licensing

- Keys live only in `.env` (git-ignored). Never print keys, never write them to logs, fixtures or tests.
- Never commit `Source/`, `data/`, brochure PDFs, rendered pages, extracted label sets or any MSCI RCA rows. Brochures are copyright of the agents; RCA is licensed (DUA).
- In code, reference documents by file name and `sha256`, market data by query text and result hash.
- RCA queries always filter `ACTIVE_FG = TRUE` and pull a small sample only.

## 7. Time rules

- If something does not work after 15 minutes, switch to the fallback in the plan and tell the integrator; do not keep digging.
- Depth beats breadth: one end-to-end chain (extract → verify → evaluate → proforma → explain → diff) that works on 15 brochures beats more features.
- A feature that is not in checkpoint 2 (22:30) is cut from the demo, not fixed.
- Code freeze at 22:40 regardless of state.

## 8. Checks before reporting done

```bash
pytest -q
git grep --untracked -nP '\p{Cyrillic}' || echo "no cyrillic"
git grep --untracked -nE '(xai-[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,}|eyJ[h]bGci)' || echo "no keys"
```
