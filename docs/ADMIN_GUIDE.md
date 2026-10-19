# Admin guide

## Requirements

| Component | Requirement |
|-----------|-------------|
| Python | 3.14+, managed with `uv` |
| Database | PostgreSQL 13+ with the IntelMQ `events` table ([schema](https://github.com/certtools/intelmq/blob/develop/intelmq/tests/bin/initdb.sql)) |
| LLM | OpenAI-compatible `/v1/chat/completions` endpoint whose model supports tool calling |
| Hardware | The tool itself is tiny. The LLM sets the requirements (e.g. a 27B model needs roughly 18 GB of (V)RAM). |

## Install and update

```bash
uv sync --locked          # install exactly the pinned, hash-checked versions from uv.lock
cp env.example .env       # first install only
uv run eventdb-mcp check
```

Update: `git pull && uv sync --locked`.

## Configuration

All settings live in `.env` (git-ignored); [`env.example`](../env.example) documents every variable.
Real environment variables override `.env`. Parsing is strict: a malformed value (e.g.
`EVENTDB_MAX_ROWS=abc`) or an unknown `EVENTDB_*`/`MCP_*`/`LLM_*` variable stops the program
with a message naming every problem. Groups:

- `EVENTDB_*`: database connection, table name, timeout, row cap, superuser guard
- `MCP_*`: FastMCP server transport (`stdio` or `http`), host, port
- `LLM_*`: OpenAI-compatible endpoint, API key, model, step limit, timeout
- `EVENTDB_MCP_LOG_FILE`: audit log

## Create a read-only database role (recommended)

The server enforces read-only transactions, a statement timeout and a single-`SELECT` rule.
A **superuser** can still call dangerous functions inside a read-only transaction
(`pg_read_file()`, `pg_terminate_backend()`, ...), and SQL comes from an LLM that reads
attacker-controlled event data. So the server refuses superuser roles unless
`EVENTDB_ALLOW_SUPERUSER=true`. Create a dedicated role instead:

```sql
CREATE ROLE eventdb_ro LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE "intelmq-events" TO eventdb_ro;
GRANT USAGE ON SCHEMA public TO eventdb_ro;
GRANT SELECT ON events TO eventdb_ro;            -- add more tables/views if wanted
ALTER ROLE eventdb_ro SET default_transaction_read_only = on;
ALTER ROLE eventdb_ro SET statement_timeout = '60s';
```

Put the password in `~/.pgpass` rather than in `EVENTDB_DSN`.

## Remote eventdb over SSH

If the database only accepts local connections, forward its Unix socket. PostgreSQL then sees
your SSH user, so `peer` authentication works without a database password:

```bash
mkdir -p tmp/pgsock
ssh -f -N -o ExitOnForwardFailure=yes -o StreamLocalBindUnlink=yes \
    -L "$PWD/tmp/pgsock/.s.PGSQL.5432:/var/run/postgresql/.s.PGSQL.5432" eventdb-host
```

```ini
# .env
EVENTDB_DSN=postgresql:///intelmq-events?host=/absolute/path/to/eventdb-mcp/tmp/pgsock&user=youruser
```

Stop the tunnel with `pkill -f 'tmp/pgsock/.s.PGSQL.5432'`.

## Running the MCP server

| Mode | Command | When |
|------|---------|------|
| stdio | `uv run eventdb-mcp serve` | An MCP client starts the server itself (Claude Desktop, IDEs). Default. |
| HTTP | `uv run eventdb-mcp serve --transport http` | Long-running service at `http://MCP_HOST:MCP_PORT/mcp`. |

The HTTP mode has **no authentication**. Keep `MCP_HOST=127.0.0.1`, or put it behind a reverse
proxy that authenticates users. Anyone who reaches it can read the whole eventdb.

## Monitoring and logs

- Audit log: `logs/eventdb-mcp.log` (`EVENTDB_MCP_LOG_FILE`). Every SQL statement and every LLM
  tool call is logged with a timestamp. Rotate it with `logrotate` or `newsyslog`.
- Health check: `uv run eventdb-mcp check` (exit code 0 = database and LLM reachable).

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `✗ Invalid configuration: ...` / `Unknown variable(s) in .env` (exit code 2) | A value in `.env` is malformed or out of range, or a variable name has a typo. The message names every offending variable. |
| `✗ database ... fe_sendauth: no password supplied` | Add the password to `~/.pgpass`, or use the SSH socket forward above. |
| `Refusing to run queries as a PostgreSQL superuser` | Use a read-only role (above), or knowingly set `EVENTDB_ALLOW_SUPERUSER=true`. |
| `canceling statement due to statement timeout` | Query too broad: narrow the time range, or raise `EVENTDB_STATEMENT_TIMEOUT_MS`. |
| `✗ LLM ... Connection refused` | LLM server not running or wrong `LLM_BASE_URL` (ollama: `http://localhost:11434/v1`). |
| `? LLM` (yellow) | Server is up but `LLM_MODEL` is not in its model list: fix the name or pull the model. |
| Answers without tool calls / made-up numbers | The model does not support tool calling well. Use a stronger tool-calling model. |
| `No final answer after N tool-calling steps` | Question too vague, or raise `LLM_MAX_STEPS`. |
