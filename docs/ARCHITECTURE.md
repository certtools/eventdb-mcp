# Architecture

```text
 ┌──────────────┐  OpenAI chat API   ┌──────────────────────┐
 │ local LLM    │◄──────────────────►│ chat.py  (ask loop)  │◄── eventdb-mcp ask "..."
 │ (ollama,...) │  tools + results   └──────────┬───────────┘
 └──────────────┘                               │ MCP (in-memory)
                                                ▼
 Claude Desktop / IDE / agent ──MCP stdio|http─►┌──────────────────────┐
                                                │ server.py (FastMCP)  │ 5 read-only tools
                                                └──────────┬───────────┘
                                                           │ run_query()
                                                ┌──────────▼───────────┐
                                                │ db.py  (guard +      │ READ ONLY txn,
                                                │ psycopg)             │ statement_timeout
                                                └──────────┬───────────┘
                                                           ▼
                                                PostgreSQL IntelMQ eventdb
```

## Modules (`src/eventdb_mcp/`)

| Module | Responsibility |
|--------|----------------|
| `config.py` | `Settings` (pydantic, strict), read from environment and `.env`. Single source of all knobs. |
| `db.py` | `check_sql()` (single SELECT/WITH only) and `run_query()` (read-only transaction, timeout, superuser guard, row cap, JSON-friendly rows). |
| `server.py` | `create_server(settings)` builds the FastMCP server with the tools. Structured queries are composed with `psycopg.sql` (identifiers quoted, values as parameters). |
| `charts.py` | The `chart` tool's parts: group_by whitelist, time axis and bucket choice (at most 1,000 points), the ranking SQL (buckets as `floor(epoch / step)`, which PostgreSQL 13 can do; `dense_rank`), dense series with zeros for empty buckets. |
| `chat.py` | `ask()`: OpenAI-compatible tool-calling loop against the MCP server. |
| `cli.py` | `click` commands `serve`, `ask`, `check`; sets up the audit log. |
| `__main__.py` | `python -m eventdb_mcp` (used by the end-to-end tests). |
| `data/harmonization.json` | IntelMQ field types and descriptions (from IntelMQ's `harmonization.conf`), used by `describe_table` and to validate column names. |

Outside the package, `evals/` holds the golden-question eval and benchmark (see
[EVALS.md](EVALS.md)) and the throwaway-PostgreSQL helper the tests share.

## Design decisions

- **The MCP server is the only path to the database.** The chat client talks to it through
  real MCP (in-memory transport), so anything that works in `ask` works for every MCP client.
- **Structured tools first, SQL last.** `search_events` and `event_stats` are index-friendly
  and cannot be misused; `run_sql` covers everything else. Small local models do better with
  the structured tools. `chart` returns a chart spec instead of rows (the format chat-ui
  and clickhouse-mcp share), so a UI can draw it without the LLM copying numbers.
- **Defence in depth for LLM-written SQL**, because event data is attacker-controlled
  (prompt injection): statement allow-list → read-only transaction locked by an early snapshot
  → statement timeout → superuser refusal → row cap → audit log. See `db.py`.
- **One connection per query.** Simple, no pool state; fine for interactive use.
- **Errors go back to the LLM as text** so it can correct itself (e.g. wrong column name →
  `describe_table` → retry).
