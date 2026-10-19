"""Tests for the MCP tools, called through a real in-memory MCP client."""

import asyncio
import re

from fastmcp import Client

from eventdb_mcp import server
from tests.conftest import FIXTURES


def call(settings, tool: str, **args):
    """Call ``tool`` on a fresh server; return the CallToolResult."""

    async def _call():
        async with Client(server.create_server(settings)) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    return asyncio.run(_call())


def test_harmonization_matches_intelmq_schema():
    sql_text = (FIXTURES / "initdb.sql").read_text()
    columns = set(re.findall(r'^\s+"([a-z_.0-9]+)" ', sql_text, re.MULTILINE)) - {"id"}
    assert columns == set(server.field_docs())
    assert set(server.DEFAULT_COLUMNS) - {"id"} <= columns
    assert {col for col, _ in server.FILTERS.values()} <= columns


def test_build_where():
    where, params = server.build_where({"source_asn": 64496, "feed_name": None, "since": "2026-01-01"})
    text = where.as_string()
    assert text == ' WHERE "source.asn" = %s AND "time.source" >= %s'
    assert params == [64496, "2026-01-01"]
    assert not server.build_where({"source_ip": None})[1]


def test_tools_listed(settings):
    async def _list():
        async with Client(server.create_server(settings)) as client:
            return {t.name for t in await client.list_tools()}

    assert asyncio.run(_list()) == {
        "list_tables",
        "describe_table",
        "search_events",
        "event_stats",
        "run_sql",
    }


def test_list_tables(settings):
    rows = call(settings, "list_tables").structured_content["rows"]
    assert {"schema": "public", "name": "events", "kind": "table"}.items() <= rows[0].items()


def test_describe_table(settings):
    rows = call(settings, "describe_table").structured_content["rows"]
    by_name = {r["column_name"]: r for r in rows}
    assert by_name["source.ip"]["data_type"] == "inet"
    assert "ip observed" in by_name["source.ip"]["description"]
    assert by_name["id"]["description"] == ""


def test_search_events_filters_by_cidr_and_time(settings):
    result = call(settings, "search_events", source_ip="192.0.2.0/24", limit=10).structured_content
    assert result["row_count"] == 3
    assert {r["source.asn"] for r in result["rows"]} == {64496}
    assert "raw" not in result["columns"]
    times = [r["time.source"] for r in result["rows"]]
    assert times == sorted(times, reverse=True)

    recent = call(
        settings, "search_events", classification_type="scanner", since="2000-01-01"
    ).structured_content
    assert recent["row_count"] == 2


def test_event_stats(settings):
    rows = call(settings, "event_stats", group_by="source.geolocation.cc").structured_content["rows"]
    assert rows[0] == {"value": "AT", "events": 5}
    rows = call(settings, "event_stats", group_by="malware.name", malware_name="mirai").structured_content[
        "rows"
    ]
    assert rows == [{"value": "mirai", "events": 2}]


def test_event_stats_rejects_unknown_column(settings):
    result = call(settings, "event_stats", group_by='"; DROP TABLE events; --')
    assert result.is_error
    assert "Unknown column" in result.content[0].text


def test_run_sql(settings):
    result = call(settings, "run_sql", query='SELECT DISTINCT "feed.name" FROM events ORDER BY 1')
    assert [r["feed.name"] for r in result.structured_content["rows"]][0] == "Phishing URLs"


def test_run_sql_rejects_writes(settings):
    result = call(settings, "run_sql", query="DELETE FROM events")
    assert result.is_error
    assert "Only SELECT" in result.content[0].text
