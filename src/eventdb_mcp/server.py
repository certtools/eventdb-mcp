"""FastMCP server exposing the IntelMQ eventdb as read-only tools.

Tools:

* ``list_tables``     - tables/views in the database with estimated row counts
* ``describe_table``  - columns of a table, with IntelMQ field descriptions
* ``search_events``   - structured search (safe, index-friendly, no SQL needed)
* ``event_stats``     - "top N" counts grouped by one column
* ``chart``           - events per hour/day, one line per top value (a chart the UI draws)
* ``run_sql``         - free-form read-only SELECT for everything else
"""

import html
import json
from datetime import timedelta
from functools import cache
from importlib import resources
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from psycopg import sql
from pydantic import Field

from eventdb_mcp import charts, db
from eventdb_mcp.config import Settings

INSTRUCTIONS = """\
Read-only access to an IntelMQ eventdb (PostgreSQL). Each row of the events
table is one security event (IOC) collected by IntelMQ. Column names contain
dots and MUST be double-quoted in SQL, e.g. "source.ip", "time.source",
"classification.type". "time.source" is when the event happened according to
the feed; "time.observation" is when IntelMQ saw it. Both are indexed, as are
"source.ip", "source.asn", "source.fqdn", "feed.name", "feed.code",
"classification.*" and "source.abuse_contact". Always restrict large queries by
time (e.g. "time.source" >= now() - interval '7 days') and use LIMIT.
Never add up COUNT(DISTINCT ...) values of several groups to get a total
(the same IP can appear in many groups); query the total directly.
Prefer search_events / event_stats; use chart for plots and trends over time;
use run_sql for anything else.
"""

# Columns returned by search_events (the full "raw" payload is left out on purpose).
DEFAULT_COLUMNS = [
    "id",
    "time.source",
    "time.observation",
    "feed.name",
    "classification.taxonomy",
    "classification.type",
    "classification.identifier",
    "source.ip",
    "source.port",
    "source.asn",
    "source.as_name",
    "source.fqdn",
    "source.url",
    "source.geolocation.cc",
    "source.abuse_contact",
    "destination.ip",
    "destination.port",
    "protocol.application",
    "malware.name",
    "severity",
    "tlp",
]

METADATA_ROWS = 1000  # schema metadata is never cut off by EVENTDB_MAX_ROWS

# search_events / event_stats argument -> (column, SQL operator)
FILTERS = {
    "source_ip": ("source.ip", "<<="),  # inet containment: an IP or a CIDR network
    "source_asn": ("source.asn", "="),
    "source_fqdn": ("source.fqdn", "="),
    "source_cc": ("source.geolocation.cc", "="),
    "classification_taxonomy": ("classification.taxonomy", "="),
    "classification_type": ("classification.type", "="),
    "classification_identifier": ("classification.identifier", "="),
    "feed_name": ("feed.name", "="),
    "malware_name": ("malware.name", "="),
    "since": ("time.source", ">="),
    "until": ("time.source", "<"),
}

# Reusable parameter annotations (shown to the LLM as the JSON schema).
Ip = Annotated[str | None, Field(description="Source IP or CIDR, e.g. 192.0.2.1 or 192.0.2.0/24")]
Asn = Annotated[int | None, Field(description="Source autonomous system number, e.g. 64496")]
Fqdn = Annotated[str | None, Field(description="Exact source FQDN, e.g. www.example.com")]
Cc = Annotated[str | None, Field(description="Two-letter source country code, e.g. AT")]
Taxonomy = Annotated[str | None, Field(description="e.g. malicious-code, vulnerable, abusive-content")]
CType = Annotated[str | None, Field(description="e.g. infected-system, phishing, scanner, ddos")]
Ident = Annotated[str | None, Field(description="e.g. openresolver, mirai, heartbleed")]
Feed = Annotated[str | None, Field(description="Exact feed name, see event_stats(group_by='feed.name')")]
Malware = Annotated[str | None, Field(description="Lowercase malware name, e.g. mirai")]
Since = Annotated[str | None, Field(description="ISO timestamp, inclusive lower bound on time.source")]
Until = Annotated[str | None, Field(description="ISO timestamp, exclusive upper bound on time.source")]


@cache
def field_docs() -> dict[str, dict]:
    """IntelMQ harmonization: column name -> {type, description}."""
    text = resources.files("eventdb_mcp").joinpath("data/harmonization.json").read_text("utf-8")
    return json.loads(text)["fields"]


def build_where(filters: dict) -> tuple[sql.Composable, list]:
    """Turn ``{argument: value}`` into a ``WHERE`` clause plus parameters (``None`` = unused)."""
    parts, params = [], []
    for arg, value in filters.items():
        if value is None:
            continue
        column, op = FILTERS[arg]
        parts.append(sql.SQL("{} {} %s").format(sql.Identifier(column), sql.SQL(op)))
        params.append(value)
    if not parts:
        return sql.SQL(""), params
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def _known_column(column: str) -> str:
    if column != "id" and column not in field_docs():
        raise ToolError(f"Unknown column {column!r}. Use describe_table to list columns.")
    return column


def create_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server; all tools use ``settings`` for DB access."""
    mcp = FastMCP("IntelMQ eventdb", instructions=INSTRUCTIONS)
    table = sql.Identifier(settings.db_table)

    def run(query, params=None, max_rows=None) -> dict:
        try:
            return db.run_query(settings, query, params, max_rows)
        except db.QueryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_tables() -> dict:
        """List tables and views in the eventdb with their estimated number of rows."""
        return run(
            "SELECT n.nspname AS schema, c.relname AS name,"
            " CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table' ELSE 'view' END AS kind,"
            " GREATEST(c.reltuples, 0)::bigint AS estimated_rows"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'p', 'v', 'm')"
            " AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
            " AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1, 2",
            max_rows=METADATA_ROWS,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def describe_table(
        table_name: Annotated[str, Field(description="Table or view name")] = settings.db_table,
    ) -> dict:
        """Columns of a table with SQL type and the IntelMQ field description."""
        result = run(
            sql.SQL(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = %s ORDER BY ordinal_position"
            ),
            [table_name],
            max_rows=METADATA_ROWS,
        )
        docs = field_docs()
        for row in result["rows"]:
            row["description"] = docs.get(row["column_name"], {}).get("description", "")
        return result

    @mcp.tool(annotations={"readOnlyHint": True})
    def search_events(  # pylint: disable=too-many-arguments,too-many-locals,unused-argument
        *,
        source_ip: Ip = None,
        source_asn: Asn = None,
        source_fqdn: Fqdn = None,
        source_cc: Cc = None,
        classification_taxonomy: Taxonomy = None,
        classification_type: CType = None,
        classification_identifier: Ident = None,
        feed_name: Feed = None,
        malware_name: Malware = None,
        since: Since = None,
        until: Until = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
    ) -> dict:
        """Find events matching ALL given filters, newest first (by time.source)."""
        args = {k: v for k, v in locals().items() if k in FILTERS}
        where, params = build_where(args)
        query = (
            sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(map(sql.Identifier, DEFAULT_COLUMNS)), table
            )
            + where
            + sql.SQL(' ORDER BY "time.source" DESC NULLS LAST LIMIT %s')
        )
        return run(query, [*params, limit])

    @mcp.tool(annotations={"readOnlyHint": True})
    def event_stats(  # pylint: disable=too-many-arguments,too-many-locals,unused-argument
        group_by: Annotated[str, Field(description='Column to group by, e.g. "source.asn"')],
        *,
        source_ip: Ip = None,
        source_asn: Asn = None,
        source_fqdn: Fqdn = None,
        source_cc: Cc = None,
        classification_taxonomy: Taxonomy = None,
        classification_type: CType = None,
        classification_identifier: Ident = None,
        feed_name: Feed = None,
        malware_name: Malware = None,
        since: Since = None,
        until: Until = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 20,
    ) -> dict:
        """Count events per value of ``group_by`` (top N), optionally filtered."""
        args = {k: v for k, v in locals().items() if k in FILTERS}
        where, params = build_where(args)
        column = sql.Identifier(_known_column(group_by))
        query = (
            sql.SQL("SELECT {} AS value, count(*) AS events FROM {}").format(column, table)
            + where
            + sql.SQL(" GROUP BY 1 ORDER BY 2 DESC LIMIT %s")
        )
        return run(query, [*params, limit])

    def chart_window(since: str | None, until: str | None, filters: dict) -> dict:
        """``{start, end, window}``; until defaults to the day after the latest matching event."""
        latest = None
        if until is None:
            where, params = build_where(filters)
            latest_sql = sql.SQL('SELECT max("time.source") AS latest FROM {}').format(table) + where
            latest = run(latest_sql, params)["rows"][0]["latest"]
            if latest is None:
                raise ToolError("No events match these filters.")
        end = charts.parse_time("until", until or latest)
        end = end if until else charts.floor(end, "day") + timedelta(days=1)
        start = charts.parse_time("since", since) if since else end - timedelta(days=charts.DEFAULT_DAYS)
        if start >= end and latest:
            raise ToolError(f"No events since {start}: the latest matching event is from {latest}.")
        if start >= end:
            raise ToolError(f"since must be before until (since {start}, until {end}).")
        window = {"since": start.isoformat(), "until": end.isoformat(), "time_zone": "UTC"}
        defaults = [f"until = the day after the latest matching event ({latest})"] if latest else []
        defaults += [f"since = {charts.DEFAULT_DAYS} days before until"] if since is None else []
        if defaults:
            window["note"] = "Defaults: " + "; ".join(defaults) + "."
        return {"start": start, "end": end, "window": window}

    @mcp.tool(annotations={"readOnlyHint": True})
    def chart(  # pylint: disable=too-many-arguments,too-many-locals,unused-argument
        group_by: Annotated[
            str | None,
            Field(
                description="One line per top value of this column: feed.name, classification.taxonomy,"
                " classification.type, classification.identifier, malware.name, source.asn,"
                " source.geolocation.cc, ... (see the error for the full list); leave out for one total line"
            ),
        ] = None,
        *,
        bucket: Annotated[
            Literal["10min", "hour", "day"] | None,
            Field(description="Time bucket (UTC); leave out for hour, or day when hours do not fit"),
        ] = None,
        series: Annotated[int, Field(ge=1, le=10, description="Number of lines (top values)")] = 6,
        y_scale: Annotated[
            Literal["linear", "log"], Field(description="'log' when the lines differ by orders of magnitude")
        ] = "linear",
        title: Annotated[str | None, Field(max_length=120, description="Chart title; default: built")] = None,
        source_ip: Ip = None,
        source_asn: Asn = None,
        source_fqdn: Fqdn = None,
        source_cc: Cc = None,
        classification_taxonomy: Taxonomy = None,
        classification_type: CType = None,
        classification_identifier: Ident = None,
        feed_name: Feed = None,
        malware_name: Malware = None,
        since: Since = None,
        until: Until = None,
    ) -> dict:
        """Data for a line chart that the user's UI draws: events per time bucket (time.source, UTC).

        Use it when the user asks for a plot, chart, graph, trend, timeline, or "over time" /
        "per hour" / "per day". One line per top value of group_by (most events first), or one
        "all" line. Without since/until: the 30 days up to the latest matching event. At most
        1,000 points (series x buckets). Each line has total, peak and peak_at: describe the
        chart in 2-3 sentences from those, do not read or list the values.
        """
        filters = {k: v for k, v in locals().items() if k in FILTERS and k not in ("since", "until")}
        charts.line_sql(table, group_by, sql.SQL(""))  # refuse a bad group_by before any query
        used = chart_window(since, until, filters)
        bucket, labels = charts.pick_bucket(used["start"], used["end"], bucket, series)
        where, params = build_where(filters | {"since": used["start"], "until": used["end"]})
        origin = charts.floor(used["start"], bucket)
        query = charts.line_sql(table, group_by, where)
        rows = run(query, [charts.BUCKETS[bucket], origin, *params, series], max_rows=charts.MAX_POINTS)
        shown = ", ".join(f"{FILTERS[k][0]}={v}" for k, v in filters.items() if v is not None)
        default_title = (
            f"Events per {bucket}{f' by {group_by}' if group_by else ''}{f' ({shown})' if shown else ''}"
        )
        spec = {
            "type": "line",
            # LLMs sometimes write "&lt;1024": the UI shows titles as plain text.
            "title": html.unescape(title) if title else default_title,
            "x": {
                "label": f"Time (UTC), per {bucket}",
                "start": labels[0],
                "step": bucket,
                "count": len(labels),
            },
            "y": {"label": f"events per {bucket}", "scale": y_scale},
            "series": charts.line_series(rows["rows"], labels),
        }
        return {"chart": spec, "window": used["window"]}

    @mcp.tool(annotations={"readOnlyHint": True})
    def run_sql(
        query: Annotated[str, Field(description="One read-only SELECT statement")],
    ) -> dict:
        """Run a read-only SELECT. Double-quote dotted column names; always use LIMIT."""
        return run(query)

    return mcp
