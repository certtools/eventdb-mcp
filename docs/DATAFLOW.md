# Data flow

## `eventdb-mcp ask "QUESTION"`

1. `cli.py` loads `.env` into `Settings` and opens the audit log (`EVENTDB_MCP_LOG_FILE`).
2. `chat.ask()` starts an in-memory MCP session with `server.create_server(settings)` and
   lists its tools.
3. It sends to `LLM_BASE_URL/chat/completions`: a system prompt (role, current UTC time,
   eventdb hints), the question, and the tool list (JSON schemas).
4. For every tool call the LLM returns:
   - the call is logged (`tool call: NAME ARGS`),
   - the MCP tool runs → `db.run_query()`:
     1. plain SQL strings pass `check_sql()` (one `SELECT`/`WITH` statement),
     2. connect with `statement_timeout`, start a `READ ONLY` transaction,
     3. probe `pg_roles.rolsuper` (takes the snapshot; refuses superusers unless allowed),
     4. log the SQL (`SQL: ... | params=...`), execute, fetch at most `max_rows + 1` rows,
     5. roll back, convert values (timestamps, inet, ...) to JSON-friendly strings,
   - the result (or the error text) goes back to the LLM as a `tool` message, cut at
     20 000 characters.
5. Repeat until the LLM answers without tool calls, or `LLM_MAX_STEPS` is reached
   (then a fixed "No final answer" message is returned instead of failing silently).
6. The answer is rendered as Markdown on stdout.

## `eventdb-mcp serve`

MCP client → (stdio or HTTP `/mcp`) → FastMCP tool → `db.run_query()` (steps 4.1–4.5 above)
→ JSON result `{columns, rows, row_count, truncated}` → MCP client.

## What leaves the machine

| Data | Goes to |
|------|---------|
| SQL and parameters | PostgreSQL (`EVENTDB_DSN`) |
| Question, tool schemas, **query results (event data)** | the LLM at `LLM_BASE_URL`. Use a local LLM for TLP:AMBER/RED data. |
| SQL, tool calls | audit log file |
