"""Local smoke test: list tools and call explain_figure through the MCP client.

Run the server first, then: .venv/bin/python -m mcp_server.smoke [doc_id] [cell_name]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

load_dotenv()


async def run(doc_id: str, cell_name: str) -> None:
    url = os.getenv("MCP_URL", f"http://127.0.0.1:{os.getenv('MCP_PORT', '8765')}/mcp")
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"})
    async with client, streamable_http_client(url, http_client=client) as (read, write), \
            ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", sorted(t.name for t in tools.tools))
        res = await session.call_tool("explain_figure", {"doc_id": doc_id, "cell_name": cell_name})
        if res.is_error:
            raise SystemExit(f"explain_figure failed: {res.content}")
        data = res.structured_content or json.loads(res.content[0].text)
        print(data["explain_text"])
        print("sources:", len(data["sources"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(run(args[0] if args else "acuitus-90f42467", args[1] if len(args) > 1 else "value"))
