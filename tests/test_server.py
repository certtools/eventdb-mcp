"""Tests for the MCP tools, called through a real in-memory MCP client."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from fastmcp import Client

from eventdb_mcp import charts, server
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
        "chart",
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


def ago(days: float) -> str:
    """ISO timestamp ``days`` before now (the sample events are relative to now())."""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def test_chart_lines_per_top_value(settings):
    result = call(settings, "chart", group_by="classification.type", since=ago(14), bucket="day")
    spec = result.structured_content["chart"]
    assert spec["type"] == "line"
    assert spec["title"] == "Events per day by classification.type"
    assert spec["x"]["step"] == "day"
    assert spec["x"]["label"] == "Time (UTC), per day"
    assert spec["y"] == {"label": "events per day", "scale": "linear"}
    names = [(s["name"], s["total"]) for s in spec["series"]]
    # Ties (2 events) are ordered by name; the scanner event 40 days ago is outside the window.
    assert names == [("infected-system", 3), ("phishing", 2), ("vulnerable-system", 2), ("scanner", 1)]
    for line in spec["series"]:
        assert len(line["values"]) == spec["x"]["count"]
        assert sum(line["values"]) == line["total"]
        assert line["peak"] == max(line["values"])
    window = result.structured_content["window"]
    assert window["time_zone"] == "UTC"
    assert "latest matching event" in window["note"]


def test_chart_defaults_to_30_days_up_to_the_latest_event(settings):
    result = call(settings, "chart").structured_content
    [line] = result["chart"]["series"]
    assert (line["name"], line["total"]) == ("all", 8)  # 9 events, one is 40 days old
    # One line: the point budget is for one series, so 30 days fit hourly (720 buckets).
    assert result["chart"]["x"]["step"] == "hour"
    assert result["chart"]["x"]["count"] == 720


def test_chart_without_group_by_fits_a_year_of_days(settings):
    spec = call(settings, "chart", since=ago(365), until=ago(0)).structured_content["chart"]
    assert spec["x"]["step"] == "day" and spec["x"]["count"] in (365, 366)
    assert [(s["name"], s["total"]) for s in spec["series"]] == [("all", 9)]


def test_chart_filters_and_series_limit(settings):
    spec = call(settings, "chart", feed_name="Scanners", since=ago(60), until=ago(0)).structured_content
    assert [(s["name"], s["total"]) for s in spec["chart"]["series"]] == [("all", 2)]
    assert "note" not in spec["window"]
    spec = call(settings, "chart", group_by="source.geolocation.cc", series=1, since=ago(60))
    assert [(s["name"], s["total"]) for s in spec.structured_content["chart"]["series"]] == [("AT", 5)]


def test_chart_title_and_log_scale(settings):
    spec = call(settings, "chart", title="Ports &lt;1024", y_scale="log").structured_content["chart"]
    assert spec["title"] == "Ports <1024"
    assert spec["y"]["scale"] == "log"


def test_chart_sends_exact_sql_and_parameters(settings, monkeypatch):
    seen = []

    def fake_run_query(_settings, query, params=None, max_rows=None):
        seen.append((query, params, max_rows))
        return {"rows": [{"value": "x", "t": "2026-03-02 00:00", "events": 4, "total": 4}]}

    monkeypatch.setattr(server.db, "run_query", fake_run_query)
    result = call(
        settings,
        "chart",
        group_by="feed.name",
        feed_name="Scanners",
        since="2026-03-01T12:00",
        until="2026-03-04",
    ).structured_content
    assert len(seen) == 1  # since and until given: no lookup of the latest event
    query, params, max_rows = seen[0]
    assert (
        query.as_string()
        == charts.line_sql(
            server.sql.Identifier("events"),
            "feed.name",
            server.sql.SQL(' WHERE "feed.name" = %s AND "time.source" >= %s AND "time.source" < %s'),
        ).as_string()
    )
    start, end = datetime(2026, 3, 1, 12, tzinfo=UTC), datetime(2026, 3, 4, tzinfo=UTC)
    assert params == [3600, 3600, "Scanners", start, end, 6]
    assert max_rows == charts.MAX_POINTS
    # Hourly buckets from 12:00: 2026-03-02 00:00 is bucket 12, the others are 0.
    assert result["chart"]["series"][0]["values"][11:13] == [0, 4]
    assert result["window"] == {"since": start.isoformat(), "until": end.isoformat(), "time_zone": "UTC"}


def test_chart_refusals(settings):
    cases = [
        ({"group_by": '"; DROP TABLE events; --'}, "group_by must be one of"),
        ({"group_by": "raw"}, "group_by must be one of"),
        ({"since": "2026-03-02", "until": "2026-03-01"}, "since must be before until"),
        ({"since": "yesterday"}, "since: not an ISO date"),
        ({"since": ago(-2)}, "No events since"),
        ({"since": ago(30), "bucket": "10min"}, "Too many points"),
        ({"feed_name": "No such feed"}, "No events match"),
        ({"since": "2001-01-01", "until": "2001-01-02"}, "No events match"),  # explicit, empty window
        ({"since": ago(0.02)}, "No events"),  # after the latest event (1 hour ago), same day
    ]
    for args, message in cases:
        result = call(settings, "chart", **args)
        assert result.is_error, args
        assert message in result.content[0].text, (args, result.content[0].text)
