"""Tests for the SQL guard and the read-only query runner."""

from dataclasses import replace

import pytest

from eventdb_mcp import db


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "  select * from events limit 1;  ",
        "WITH x AS (SELECT 1) SELECT * FROM x",
    ],
)
def test_check_sql_accepts_select(query):
    assert db.check_sql(query).lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "query",
    [
        "",
        ";",
        "DELETE FROM events",
        "SELECT 1; DELETE FROM events",
        "UPDATE events SET comment = 'x'",
        "COPY events TO '/tmp/x'",
        "selectx 1",
    ],
)
def test_check_sql_rejects(query):
    with pytest.raises(db.QueryError):
        db.check_sql(query)


def test_run_query_returns_json_friendly_rows(settings):
    result = db.run_query(settings, 'SELECT "source.ip", "time.source", "source.asn" FROM events')
    assert result["columns"] == ["source.ip", "time.source", "source.asn"]
    assert result["row_count"] == settings.max_rows == 5
    assert result["truncated"] is True
    first = result["rows"][0]
    assert isinstance(first["source.ip"], str)  # inet -> str
    assert isinstance(first["time.source"], str)  # timestamptz -> str
    assert isinstance(first["source.asn"], int)


def test_run_query_not_truncated(settings):
    result = db.run_query(settings, "SELECT count(*) AS n FROM events")
    assert result["rows"] == [{"n": 9}]
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "query",
    [
        # data-modifying CTE passes the guard but must fail in the read-only transaction
        "WITH d AS (DELETE FROM events RETURNING id) SELECT count(*) FROM d",
        # the snapshot is already taken, so read-only mode cannot be switched off
        "SELECT set_config('transaction_read_only', 'off', true)",
        "SELECT pg_sleep(5)",  # statement_timeout
    ],
)
def test_read_only_and_timeout_enforced_even_for_superuser(su_settings, query):
    s = replace(su_settings, allow_superuser=True, statement_timeout_ms=500)
    with pytest.raises(db.QueryError, match="Database error"):
        db.run_query(s, query)
    assert db.run_query(s, "SELECT count(*) AS n FROM events")["rows"] == [{"n": 9}]


def test_superuser_refused_by_default(su_settings):
    with pytest.raises(db.QueryError, match="superuser"):
        db.run_query(su_settings, "SELECT 1")


def test_permission_error_is_query_error(settings):
    with pytest.raises(db.QueryError, match="Database error"):
        db.run_query(settings, "SELECT pg_read_file('/etc/hosts')")
