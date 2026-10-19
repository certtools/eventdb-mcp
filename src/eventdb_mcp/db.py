"""Read-only access to the IntelMQ eventdb (PostgreSQL).

Safety model (defence in depth, because SQL may come from an LLM and event
data is attacker-controlled, i.e. prompt injection is a real threat):

1. :func:`check_sql` only lets a single ``SELECT`` / ``WITH`` statement through.
2. Every query runs in a ``READ ONLY`` transaction with a ``statement_timeout``.
   A first probe query takes the snapshot, so the transaction can no longer be
   switched to read-write (``SET TRANSACTION READ WRITE`` fails after that).
3. Superuser roles are refused unless ``EVENTDB_ALLOW_SUPERUSER=true``:
   read-only transactions do not stop a superuser from calling functions such
   as ``pg_read_file()`` or ``pg_terminate_backend()``.
4. The transaction is always rolled back; the result is capped at ``max_rows``.

Use a dedicated role with ``SELECT`` only (see docs/ADMIN_GUIDE.md).
"""

import json
import logging
import re
from typing import Any

import psycopg

from eventdb_mcp.config import Settings

log = logging.getLogger("eventdb_mcp")

_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class QueryError(Exception):
    """A query was rejected or failed. The message is safe to show to the LLM."""


def check_sql(query: str) -> str:
    """Return ``query`` without a trailing ``;`` or raise :class:`QueryError`."""
    body = query.strip().rstrip(";").strip()
    if not body:
        raise QueryError("Empty query.")
    if ";" in body:
        raise QueryError("Only a single SQL statement is allowed (no ';').")
    if not _ALLOWED_START.match(body):
        raise QueryError("Only SELECT or WITH ... SELECT queries are allowed.")
    return body


def run_query(settings: Settings, query: Any, params: Any = None, max_rows: int | None = None) -> dict:
    """Run ``query`` read-only and return ``{columns, rows, row_count, truncated}``.

    ``query`` is either a plain string (checked with :func:`check_sql`) or a
    pre-built :class:`psycopg.sql.Composed` from this package (trusted).
    ``max_rows`` overrides ``settings.max_rows`` (used for schema metadata).
    """
    max_rows = max_rows or settings.max_rows
    if isinstance(query, str):
        query = check_sql(query)
    options = f"-c statement_timeout={settings.statement_timeout_ms}"
    try:
        with psycopg.connect(settings.db_dsn, options=options, connect_timeout=10) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                # Probe query: takes the snapshot (locks read-only mode) and checks the role.
                cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                row = cur.fetchone()
                if row and row[0] and not settings.allow_superuser:
                    raise QueryError(
                        "Refusing to run queries as a PostgreSQL superuser. Use a read-only "
                        "role or set EVENTDB_ALLOW_SUPERUSER=true."
                    )
                log.info("SQL: %s | params=%s", _as_text(query, conn), params)
                cur.execute(query, params)
                columns = [c.name for c in cur.description or []]
                rows = cur.fetchmany(max_rows + 1)
            conn.rollback()
    except psycopg.Error as exc:
        log.warning("SQL failed: %s", exc)
        raise QueryError(f"Database error: {str(exc).strip()}") from exc

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    # default=str turns datetime, inet, Decimal, ... into plain strings.
    records = json.loads(json.dumps([dict(zip(columns, r, strict=True)) for r in rows], default=str))
    return {"columns": columns, "rows": records, "row_count": len(records), "truncated": truncated}


def _as_text(query: Any, conn: psycopg.Connection) -> str:
    return query if isinstance(query, str) else query.as_string(conn)
