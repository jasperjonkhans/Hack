"""Start the real MCP server over stdio (as Omnigent does) and call it through an MCP client."""

import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "find_work", "search_works", "get_work", "top_cited", "works_by_author", "review_queue", "run_sql",
    "import_work", "save_work", "add_claim", "assess_claim", "get_claim", "list_claims",
}


def call(*steps):
    """Run (tool, arguments) steps in one server session; return the results."""

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "academic_db.server"],
            env={**os.environ, "ACADEMIC_DB_AGENT": "test"},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            results = []
            for name, arguments in steps:
                if name == "list_tools":
                    results.append(await session.list_tools())
                else:
                    results.append(await session.call_tool(name, arguments))
            return results

    return asyncio.run(run())


def payload(result):
    assert not result.isError, result.content[0].text
    return result.structuredContent or json.loads(result.content[0].text)


def test_lists_every_tool_with_annotations():
    (tools,) = call(("list_tools", None))
    by_name = {tool.name: tool for tool in tools.tools}
    assert set(by_name) == EXPECTED_TOOLS
    assert by_name["run_sql"].annotations.readOnlyHint is True
    assert by_name["assess_claim"].annotations.readOnlyHint is False
    assert by_name["assess_claim"].inputSchema["properties"]["confidence"]["maximum"] == 1


@pytest.mark.db
def test_read_tools_over_stdio():
    missing, sql = call(
        ("find_work", {"identifier": "https://doi.org/10.1234/does-not-exist"}),
        ("run_sql", {"sql": "SELECT count(*) AS n FROM providers"}),
    )
    assert payload(missing) == {"found": False, "work": None}
    assert payload(sql)["rows"] == [{"n": 3}]


@pytest.mark.db
def test_errors_are_reported_to_the_agent():
    garbage, write, bad_confidence = call(
        ("find_work", {"identifier": "attention is all you need"}),
        ("run_sql", {"sql": "DELETE FROM claims"}),
        ("assess_claim", {"claim_id": 1, "confidence": 2, "verdict": "supported", "rationale": "x", "evidence": []}),
    )
    assert garbage.isError and "Unrecognized identifier" in garbage.content[0].text
    assert write.isError and "read-only" in write.content[0].text
    assert bad_confidence.isError
