"""Unit tests for charts.py: time axis, bucket choice, exact SQL, dense series."""

from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.exceptions import ToolError
from psycopg import sql

from eventdb_mcp import charts, server


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_columns_are_known_eventdb_columns():
    assert set(charts.COLUMNS) <= set(server.field_docs())


@pytest.mark.parametrize(
    ("moment", "bucket", "floored"),
    [
        ("2026-03-08 03:17:59", "10min", "2026-03-08 03:10"),
        ("2026-03-08 03:17:59", "hour", "2026-03-08 03:00"),
        ("2026-03-08 03:17:59", "day", "2026-03-08 00:00"),
        ("2026-03-08 00:00:00", "day", "2026-03-08 00:00"),
    ],
)
def test_floor(moment, bucket, floored):
    assert charts.floor(at(moment), bucket) == at(floored)


def test_floor_converts_to_utc():
    vienna = datetime.fromisoformat("2026-03-08T00:30:00+01:00")
    assert charts.floor(vienna, "day") == at("2026-03-07 00:00")


def test_axis_starts_at_the_floored_since_and_excludes_until():
    labels = charts.axis(at("2026-03-08 22:30"), at("2026-03-09 02:00"), "hour")
    assert labels == ["2026-03-08 22:00", "2026-03-08 23:00", "2026-03-09 00:00", "2026-03-09 01:00"]
    assert charts.axis(at("2026-03-01"), at("2026-03-31"), "day")[-1] == "2026-03-30 00:00"
    assert len(charts.axis(at("2026-03-01"), at("2026-03-01 00:01"), "day")) == 1


def test_pick_bucket_auto_prefers_hours_then_days():
    bucket, labels = charts.pick_bucket(at("2026-03-01"), at("2026-03-02"), None, 6)
    assert (bucket, len(labels)) == ("hour", 24)
    bucket, labels = charts.pick_bucket(at("2026-01-01"), at("2026-04-01"), None, 6)
    assert (bucket, len(labels)) == ("day", 90)


def test_pick_bucket_keeps_an_explicit_bucket():
    bucket, labels = charts.pick_bucket(at("2026-03-01"), at("2026-03-02"), "10min", 6)
    assert (bucket, len(labels)) == ("10min", 144)


def test_pick_bucket_refuses_more_than_max_points():
    # 1,000 points exactly is fine, one more bucket is not.
    assert (
        len(charts.pick_bucket(at("2026-01-01"), at("2026-01-01") + timedelta(days=100), "day", 10)[1]) == 100
    )
    with pytest.raises(ToolError, match="at most 1000"):
        charts.pick_bucket(at("2026-01-01"), at("2026-01-01") + timedelta(days=101), "day", 10)
    with pytest.raises(ToolError, match="Too many points"):
        charts.pick_bucket(at("2023-01-01"), at("2026-01-01"), None, 1)


def test_line_sql_exact_text():
    where = sql.SQL(' WHERE "feed.name" = %s')
    text = charts.line_sql(sql.Identifier("events"), "classification.type", where).as_string()
    assert text == (
        "SELECT value, t, events, total FROM ("
        "SELECT value, t, events, total, dense_rank() OVER (ORDER BY total DESC, value) AS rank FROM ("
        'SELECT "classification.type" AS value,'
        ' to_char(to_timestamp(floor(extract(epoch FROM "time.source") / %s) * %s),'
        " 'YYYY-MM-DD HH24:MI') AS t,"
        ' count(*) AS events, sum(count(*)) OVER (PARTITION BY "classification.type") AS total'
        ' FROM "events" WHERE "feed.name" = %s GROUP BY 1, 2) AS buckets) AS ranked'
        " WHERE rank <= %s ORDER BY rank, t"
    )


def test_line_sql_without_group_by_is_one_line_named_all():
    text = charts.line_sql(sql.Identifier("events"), None, sql.SQL("")).as_string()
    assert "SELECT 'all' AS value," in text
    assert "PARTITION BY 'all'" in text


def test_line_sql_refuses_columns_outside_the_whitelist():
    with pytest.raises(ToolError, match="group_by must be one of"):
        charts.line_sql(sql.Identifier("events"), "raw", sql.SQL(""))


def test_line_series_fills_missing_buckets_with_zero():
    labels = ["2026-03-08 00:00", "2026-03-09 00:00", "2026-03-10 00:00"]
    rows = [
        {"value": "scanner", "t": "2026-03-08 00:00", "events": 2, "total": 9},
        {"value": "scanner", "t": "2026-03-10 00:00", "events": 7, "total": 9},
        {"value": None, "t": "2026-03-09 00:00", "events": 1, "total": 1},
    ]
    assert charts.line_series(rows, labels) == [
        {"name": "scanner", "total": 9, "values": [2, 0, 7], "peak": 7, "peak_at": "2026-03-10 00:00"},
        {"name": "(none)", "total": 1, "values": [0, 1, 0], "peak": 1, "peak_at": "2026-03-09 00:00"},
    ]
    assert not charts.line_series([], labels)


def test_parse_time():
    assert charts.parse_time("since", "2026-03-01") == at("2026-03-01")
    assert charts.parse_time("since", "2026-03-01T01:00:00+01:00") == at("2026-03-01")
    with pytest.raises(ToolError, match="since: not an ISO date"):
        charts.parse_time("since", "last week")
