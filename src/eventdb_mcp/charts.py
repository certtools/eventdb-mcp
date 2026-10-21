"""Chart data for the ``chart`` tool: events per time bucket, one line per top value of a column.

The spec is the same as clickhouse-mcp's ``chart`` (chat-ui draws it as is, see chat-ui
docs/DEVELOPER.md "Charts")::

    {"type": "line", "title": ..., "x": {"label", "start", "step", "count"},
     "y": {"label", "scale"}, "series": [{"name", "total", "values", "peak", "peak_at"}]}

The numbers always come from PostgreSQL, never from the LLM: the SQL is built from
whitelisted identifiers (:data:`COLUMNS`) and ``%s`` parameters. Buckets are UTC (the
session time zone, see db.py), aligned to the Unix epoch (no ``date_bin``: that needs
PostgreSQL 14) and labelled ``YYYY-MM-DD HH:MM``. The x axis is
``{start, step, count}``; buckets without events are filled with 0 here, not in SQL.
"""

import math
from datetime import UTC, datetime, timedelta

from fastmcp.exceptions import ToolError
from psycopg import sql

MAX_POINTS = 1000  # series x buckets
DEFAULT_DAYS = 30  # window when since is left out
LABEL_FORMAT = "%Y-%m-%d %H:%M"
BUCKETS = {"10min": timedelta(minutes=10), "hour": timedelta(hours=1), "day": timedelta(days=1)}
AUTO_BUCKETS = ("hour", "day")  # 10min only when asked for: feeds rarely report that finely
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # buckets are aligned like the SQL: floor(epoch / step)

# Columns a chart may group by: categorical, low-cardinality IntelMQ fields.
COLUMNS = (
    "feed.name",
    "feed.provider",
    "feed.code",
    "classification.taxonomy",
    "classification.type",
    "classification.identifier",
    "malware.name",
    "source.asn",
    "source.as_name",
    "source.geolocation.cc",
    "protocol.application",
    "protocol.transport",
    "destination.port",
    "severity",
    "tlp",
)


def parse_time(name: str, text: str) -> datetime:
    """An ISO date or timestamp as an aware datetime (no offset = UTC)."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ToolError(f"{name}: not an ISO date or timestamp: {text!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def floor(moment: datetime, bucket: str) -> datetime:
    """Start (UTC) of the ``bucket`` that contains ``moment``."""
    step = BUCKETS[bucket]
    return EPOCH + (moment - EPOCH) // step * step


def axis(start: datetime, end: datetime, bucket: str) -> list[str]:
    """Labels of every ``bucket`` from ``start`` (floored) to ``end`` (exclusive)."""
    first, step = floor(start, bucket), BUCKETS[bucket]
    return [(first + i * step).strftime(LABEL_FORMAT) for i in range(math.ceil((end - first) / step))]


def pick_bucket(start: datetime, end: datetime, bucket: str | None, series: int) -> tuple[str, list[str]]:
    """``(bucket, labels)``: the given bucket, or the finest automatic one that fits MAX_POINTS."""
    for name in [bucket] if bucket else AUTO_BUCKETS:
        if math.ceil((end - floor(start, name)) / BUCKETS[name]) * series <= MAX_POINTS:
            return name, axis(start, end, name)
    raise ToolError(
        f"Too many points for one chart (at most {MAX_POINTS}: buckets x {series} series)."
        " Use a coarser bucket ('hour' or 'day'), fewer series or a shorter window."
    )


def line_sql(table: sql.Identifier, column: str | None, where: sql.Composable) -> sql.Composed:
    """Events per (value, bucket) for the top ``%s`` values of ``column`` (None = one line "all").

    Parameters, in order: bucket seconds (twice), the ``where`` parameters, series.
    """
    if column is not None and column not in COLUMNS:
        raise ToolError(f"group_by must be one of {', '.join(COLUMNS)} (or left out for one line).")
    value = sql.Identifier(column) if column else sql.SQL("'all'")
    return (
        sql.SQL(
            "SELECT value, t, events, total FROM ("
            "SELECT value, t, events, total, dense_rank() OVER (ORDER BY total DESC, value) AS rank FROM ("
            "SELECT {value} AS value,"
            ' to_char(to_timestamp(floor(extract(epoch FROM "time.source") / %s) * %s),'
            " 'YYYY-MM-DD HH24:MI') AS t,"
            " count(*) AS events, sum(count(*)) OVER (PARTITION BY {value}) AS total FROM {table}"
        ).format(value=value, table=table)
        + where
        + sql.SQL(" GROUP BY 1, 2) AS buckets) AS ranked WHERE rank <= %s ORDER BY rank, t")
    )


def line_series(rows: list[dict], labels: list[str]) -> list[dict]:
    """One dense series per value (in the rows' order), with its total and its peak."""
    position = {label: i for i, label in enumerate(labels)}
    series: dict[str, dict] = {}
    for row in rows:
        name = "(none)" if row["value"] is None else str(row["value"])
        line = series.setdefault(name, {"name": name, "total": row["total"], "values": [0] * len(labels)})
        if row["t"] in position:
            line["values"][position[row["t"]]] = row["events"]
    for line in series.values():
        peak = max(range(len(labels)), key=line["values"].__getitem__)
        line |= {"peak": line["values"][peak], "peak_at": labels[peak]}
    return list(series.values())
