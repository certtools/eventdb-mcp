"""FastMCP server exposing the IntelMQ eventdb as read-only tools.

Tools:

* ``list_tables``     - tables/views in the database with estimated row counts
* ``describe_table``  - columns of a table, with IntelMQ field descriptions
* ``search_events``   - structured search (safe, index-friendly, no SQL needed)
* ``event_stats``     - "top N" counts grouped by one column
* ``run_sql``         - free-form read-only SELECT for everything else
"""

import json
from functools import cache
from importlib import resources
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from psycopg import sql
from pydantic import Field

from eventdb_mcp import db
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
Prefer search_events / event_stats; use run_sql for anything else.
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

    @mcp.tool(annotations={"readOnlyHint": True})
    def run_sql(
        query: Annotated[str, Field(description="One read-only SELECT statement")],
    ) -> dict:
        """Run a read-only SELECT. Double-quote dotted column names; always use LIMIT."""
        return run(query)

    return mcp
