"""V7 Go extractor: upload a brochure into the TraceLease project and read the grounded tenancy schedule.

Endpoints (base https://go.v7labs.com/api, header X-API-KEY):
  POST /workspaces/{ws}/projects/{pid}/entities                                   create entity
  POST .../entities/{eid}/properties/brochure/start_file_upload                   -> file_upload_url, confirm_upload_url
  PUT  {file_upload_url}                                                          raw PDF bytes
  POST {confirm_upload_url}                                                       finish upload, triggers compute
  GET  .../entities/{eid}                                                         poll until tenancy-flat is done

The grounded JSON property uses a flat schema (plain strings): V7 left a nested {value, quote, page}
schema with AI Citations in "computing" for over 30 minutes, while the flat one finishes in ~6 min.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from core.models import PROPERTY_FIELDS, UNIT_FIELDS, ExtractionRun, Fact, valuation_date
from extract.common import doc_id_for, facts_from_payload, read_cache, write_cache

BASE = "https://go.v7labs.com/api"
FILE_SLUG = "brochure"
JSON_SLUG = "tenancy-flat"
PROMPT_VERSION = "v7-tenancy-flat-v1"
DONE = {"complete", "completed", "failed", "error", "skipped"}


def _env() -> tuple[str, str, str]:
    load_dotenv(".env")
    return os.environ["V7_API_KEY"], os.environ["V7_WORKSPACE_ID"], os.environ["V7_PROJECT_ID"]


def project_id() -> str:
    load_dotenv(".env")
    return os.environ.get("V7_PROJECT_ID", "unknown")


def _session() -> tuple[requests.Session, str]:
    key, ws, pid = _env()
    s = requests.Session()
    s.headers.update({"X-API-KEY": key, "Accept": "application/json"})
    return s, f"{BASE}/workspaces/{ws}/projects/{pid}"


def _abs(url: str) -> str:
    return url if url.startswith("http") else f"https://go.v7labs.com{url}"


def upload(pdf_path: str | Path) -> str:
    s, proj = _session()
    r = s.post(f"{proj}/entities", json={}, timeout=60)
    r.raise_for_status()
    eid = r.json()["id"]
    r = s.post(f"{proj}/entities/{eid}/properties/{FILE_SLUG}/start_file_upload",
               json={"filename": Path(pdf_path).name}, timeout=60)
    r.raise_for_status()
    urls = r.json()
    put = requests.put(urls["file_upload_url"], data=Path(pdf_path).read_bytes(),
                       headers={"Content-Type": "application/pdf"}, timeout=300)
    put.raise_for_status()
    r = s.post(_abs(urls["confirm_upload_url"]), json={}, timeout=60)
    r.raise_for_status()
    return eid


def fetch(entity_id: str) -> dict:
    s, proj = _session()
    r = s.get(f"{proj}/entities/{entity_id}", timeout=60)
    r.raise_for_status()
    return r.json()


def _field(raw: dict) -> dict:
    return (raw.get("fields") or {}).get(JSON_SLUG) or {}


def wait(entity_id: str, timeout: float = 1500, every: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        raw = fetch(entity_id)
        status = _field(raw).get("status")
        if status in DONE:
            return raw
        if time.monotonic() > deadline:
            raise TimeoutError(f"v7go entity {entity_id} still '{status}' after {timeout}s")
        time.sleep(every)


def _value(field: dict) -> Any:
    for key in ("manual_value", "tool_value"):
        v = (field.get(key) or {}).get("value")
        if v is not None:
            return v
    return None


def payload_of(raw: dict) -> dict:
    v = _value(_field(raw))
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return {}
    return v if isinstance(v, dict) else {}


POINTS_PER_INCH = 72.0
AREA_FIELDS = {"area_sqft", "total_area_sqft"}
_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_BRACKET_NUM_RE = re.compile(r"\(([\d,]+(?:\.\d+)?)\)")


def citations(field: dict) -> dict[str, tuple[int, list[float]]]:
    """object_path -> (page, bbox in PDF points) from V7 grounding.

    V7 bounding boxes are in inches from the top-left corner; the bbox is the union of the cited
    source's boxes on its first page, so it marks the cited passage (often a whole table row).
    """
    grounding = (field.get("tool_value") or {}).get("grounding") or {}
    sources = {s.get("index"): s for s in grounding.get("sources") or []}
    out: dict[str, tuple[int, list[float]]] = {}
    for claim in grounding.get("claims") or []:
        path = claim.get("object_path")
        if not path or path in out:
            continue
        for idx in claim.get("source_indices") or []:
            boxes = (sources.get(idx) or {}).get("bounding_boxes") or []
            if not boxes:
                continue
            page = boxes[0]["page"]
            same = [b for b in boxes if b["page"] == page]
            bbox = [round(min(b["xmin"] for b in same) * POINTS_PER_INCH, 2),
                    round(min(b["ymin"] for b in same) * POINTS_PER_INCH, 2),
                    round(max(b["xmax"] for b in same) * POINTS_PER_INCH, 2),
                    round(max(b["ymax"] for b in same) * POINTS_PER_INCH, 2)]
            out[path] = (int(page), bbox)
            break
    return out


def _pick(field: str, text: str) -> tuple[str, str]:
    """(value, quote) from a flat V7 string copied as printed.

    Area printed per floor "(2,818) (1,324)" is summed; a list of dates keeps the first break and
    the next rent review on or after the valuation date.
    """
    if field in AREA_FIELDS:
        nums = _BRACKET_NUM_RE.findall(text)
        if len(nums) > 1:
            return str(sum(float(n.replace(",", "")) for n in nums)), text
    if field in {"break_date", "rent_review_date"}:
        dates = _DATE_RE.findall(text)
        if len(dates) > 1:
            chosen = dates[0]
            if field == "rent_review_date":
                vd = valuation_date()
                for d in dates:
                    dd, mm, yy = (int(x) for x in d.split("/"))
                    if date(yy, mm, dd) >= vd:
                        chosen = d
                        break
            return chosen, chosen
    return text, text


def _cell(field: str, raw: Any, path: str, cites: dict[str, tuple[int, list[float]]]) -> Any:
    if isinstance(raw, dict) or raw is None:
        return raw
    text = str(raw).strip()
    if not text:
        return None
    value, quote = _pick(field, text)
    page, bbox = cites.get(path, (None, None))
    return {"value": value, "quote": quote, "page": page, "bbox": bbox}


def to_cells(payload: dict, cites: dict[str, tuple[int, list[float]]]) -> dict:
    """Flat V7 payload (plain strings) -> common {value, quote, page, bbox} cells."""
    units = []
    for i, unit in enumerate(payload.get("units") or []):
        if not isinstance(unit, dict):
            continue
        cells = {"unit_id": unit.get("unit_id")}
        for f in UNIT_FIELDS:
            cells[f] = _cell(f, unit.get(f), f"$.units[{i}].{f}", cites)
        units.append(cells)
    out: dict = {"units": units}
    for f in PROPERTY_FIELDS:
        out[f] = _cell(f, payload.get(f), f"$.{f}", cites)
    return out


def parse(raw: dict, doc_id: str, run_id: str | None, pdf_path: str | Path | None = None) -> list[Fact]:
    """Pure: raw entity JSON -> facts. V7 gives no cited text, so quote is the value as V7 copied it;
    verify/ grounds it against the page like any other extractor's quote."""
    pid = project_id()
    field = _field(raw)
    if field.get("status") in {"failed", "error"}:
        raise RuntimeError(f"v7go field failed: {field.get('error_message')}")
    payload = to_cells(payload_of(raw), citations(field))
    return facts_from_payload(payload, doc_id, extractor="v7go", author=f"v7go@{pid}",
                              model_version=f"v7go@{pid}", run_id=run_id)


def _run(doc_id: str) -> ExtractionRun:
    pid = project_id()
    return ExtractionRun(extractor="v7go", model=f"v7go@{pid}", prompt_version=PROMPT_VERSION,
                         doc_ids=[doc_id])


def extract_v7(pdf_path: str | Path) -> tuple[list[Fact], ExtractionRun]:
    doc_id = doc_id_for(pdf_path)
    run = _run(doc_id)
    raw = read_cache("v7go", doc_id)
    if raw is None:
        raw = wait(upload(pdf_path))
        write_cache("v7go", doc_id, raw)
    facts = parse(raw, doc_id, run.id, pdf_path)
    run.finished_at = datetime.now(UTC)
    return facts, run


def extract_many(paths: list[str | Path], workers: int = 8) -> list[tuple[str, list[Fact], ExtractionRun]]:
    """Upload every uncached doc first, then poll all in parallel. Raises on the first failed doc."""
    paths = [Path(p) for p in paths]
    todo = [p for p in paths if read_cache("v7go", doc_id_for(p)) is None]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        eids = dict(zip(todo, pool.map(upload, todo)))

        def finish(p: Path) -> None:
            write_cache("v7go", doc_id_for(p), wait(eids[p]))

        list(pool.map(finish, todo))
    return [(doc_id_for(p), *extract_v7(p)) for p in paths]
