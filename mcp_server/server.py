"""TraceLease MCP server for Grok Bot: the same core functions the UI uses, over Streamable HTTP.

Run: .venv/bin/python -m mcp_server.server  (path /mcp, port MCP_PORT, default 8765).
Every request needs `Authorization: Bearer $MCP_API_KEY` or `X-API-Key: $MCP_API_KEY`.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

mcp = MCPServer(
    name="tracelease",
    instructions=("TraceLease proforma for UK commercial tenancy schedules. Every figure traces to a "
                  "fact with a document, page and verbatim quote; cite them in answers."),
)


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: o.model_dump(mode="json")
                                 if hasattr(o, "model_dump") else str(o)))


def _sources(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ch in node.get("children", []):
        if ch["kind"] in ("fact", "snapshot"):
            out.append({k: ch.get(k) for k in ("kind", "id", "name", "value", "unit", "doc_id", "page",
                                                "quote", "author", "reason")})
        out.extend(_sources(ch))
    return out


def _version(doc_id: str, version_no: int | None = None) -> Any:
    from core import store

    versions = store.versions(doc_id)
    if not versions:
        raise ValueError(f"no proforma version for {doc_id}")
    if version_no is None:
        return versions[-1]
    for v in versions:
        if v.version_no == version_no:
            return v
    raise ValueError(f"{doc_id} has no version {version_no}")


@mcp.tool()
def explain_figure(doc_id: str, cell_name: str) -> dict[str, Any]:
    """Explain a proforma figure (e.g. 'value', 'total_rent', 'wault_expiry', 'passing_rent[Unit 1]')
    of the latest version: provenance tree, plain-text explanation and cited sources."""
    from core.explain import explain_by_name, explain_text

    v = _version(doc_id)
    tree = explain_by_name(v.id, cell_name)
    return _jsonable({"doc_id": doc_id, "version_id": v.id, "version_no": v.version_no,
                      "cell_id": tree["id"], "cell_name": cell_name, "value": tree["value"],
                      "unit": tree["unit"], "explain_text": explain_text(tree),
                      "sources": _sources(tree), "tree": tree})


@mcp.tool()
def get_fact_history(fact_id: str) -> dict[str, Any]:
    """Full edit history of a fact (oldest first): value, author, reason, page and quote per row."""
    from core.audit import history

    return _jsonable({"fact_id": fact_id, "history": history(fact_id)})


@mcp.tool()
def eval_summary(extractor: str | None = None) -> dict[str, Any]:
    """Extraction quality against reference labels (latest eval run per document), with sample sizes."""
    from core import store
    from evalq.export import latest_rows
    from evalq.score import summary

    rows = latest_rows(store.eval_rows(extractor=extractor))
    return _jsonable({"extractor": extractor, "rows": len(rows),
                      "summary": summary(rows, store.labels())})


@mcp.tool()
def review_queue(doc_id: str | None = None) -> dict[str, Any]:
    """Facts waiting for human review, with each extractor's value, page and quote."""
    from core.audit import review_queue as queue

    items = queue(doc_id)
    return _jsonable({"doc_id": doc_id, "count": len(items), "items": items})


@mcp.tool()
def diff_versions(doc_id: str, v1: int, v2: int) -> dict[str, Any]:
    """Cells that changed between proforma versions v1 and v2 of a document, with the triggering fact."""
    from core.audit import diff

    a, b = _version(doc_id, v1), _version(doc_id, v2)
    return _jsonable({"doc_id": doc_id, "v1": a.id, "v2": b.id, "changes": diff(a.id, b.id)})


@mcp.tool()
def correct_fact(fact_id: str, value: str | int | float, reason: str, user: str) -> dict[str, Any]:
    """Correct a fact as Grok Bot on behalf of `user`; needs a non-empty reason. Appends a new fact
    (author grok-bot:<user>) and rebuilds the proforma."""
    from core import store
    from core.audit import correct_fact as correct

    if not (reason or "").strip():
        raise ValueError("a correction needs a reason")
    if not (user or "").strip():
        raise ValueError("a correction needs a user name")
    new = correct(fact_id, value, reason, f"grok-bot:{user.strip()}")
    v = _version(new.doc_id)
    value_cell = next((c for c in store.cells(v.id) if c.name == "value"), None)
    return _jsonable({"fact": new, "fact_id": new.id, "supersedes_id": new.supersedes_id,
                      "doc_id": new.doc_id, "version_id": v.id, "version_no": v.version_no,
                      "value_cell": value_cell})


class ApiKeyMiddleware:
    """Pure ASGI so Streamable HTTP responses are not buffered."""

    def __init__(self, app: Any, key: str) -> None:
        self.app, self.key = app, key

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            given = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not (self.key and hmac.compare_digest(given, self.key)):
                body = b'{"error": "unauthorized"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def app() -> Any:
    key = os.getenv("MCP_API_KEY", "")
    if not key:
        raise RuntimeError("MCP_API_KEY is not set")
    return ApiKeyMiddleware(mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0"), key)


def main() -> None:
    import uvicorn

    uvicorn.run(app(), host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8765")))


if __name__ == "__main__":
    main()
