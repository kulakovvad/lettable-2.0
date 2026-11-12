import asyncio

import pytest

from mcp_server import server


def _call(app, headers):
    sent = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(msg):
        sent.append(msg)

    mw = server.ApiKeyMiddleware(downstream, "secret-key")
    scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(mw(scope, None, send))
    return sent[0]["status"]


def test_api_key_middleware():
    assert _call(None, {}) == 401
    assert _call(None, {"authorization": "Bearer wrong"}) == 401
    assert _call(None, {"authorization": "Bearer secret-key"}) == 200
    assert _call(None, {"x-api-key": "secret-key"}) == 200


def test_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"explain_figure", "get_fact_history", "eval_summary", "review_queue",
                     "diff_versions", "correct_fact"}


@pytest.mark.parametrize("reason,user", [("", "vadim"), ("  ", "vadim"), ("typo", "")])
def test_correct_fact_needs_reason_and_user(reason, user):
    with pytest.raises(ValueError):
        server.correct_fact("f1", 1, reason, user)


def test_sources_flattens_tree():
    tree = {"kind": "cell", "children": [
        {"kind": "cell", "children": [{"kind": "fact", "id": "f1", "page": 3, "quote": "£1", "children": []}]},
        {"kind": "snapshot", "id": "s1", "children": []}]}
    assert [s["id"] for s in server._sources(tree)] == ["f1", "s1"]
