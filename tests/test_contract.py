"""Contract snapshot: the tool names, descriptions and input schemas MCP clients see.

Any change (ours or from a fastmcp/pydantic upgrade) fails this test until the
snapshot is updated deliberately:

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py
"""

import asyncio
import json
import os

from fastmcp import Client

from eventdb_mcp import server
from eventdb_mcp.config import Settings
from tests.conftest import FIXTURES

SNAPSHOT = FIXTURES / "tool_contract.json"


def current_contract() -> dict:
    """Tool contract of a server that is never connected to a database."""

    async def _list():
        async with Client(server.create_server(Settings())) as client:
            return await client.list_tools()

    return {
        t.name: {
            "description": t.description,
            "input_schema": t.input_schema,
            "annotations": t.annotations.model_dump(exclude_none=True, by_alias=True)
            if t.annotations
            else None,
        }
        for t in sorted(asyncio.run(_list()), key=lambda t: t.name)
    }


def test_tool_contract_matches_snapshot():
    contract = current_contract()
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    assert contract == json.loads(SNAPSHOT.read_text()), (
        "MCP tool contract changed. If intended: UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py"
    )


def test_all_tools_are_read_only():
    for name, tool in current_contract().items():
        assert tool["annotations"]["readOnlyHint"] is True, name
