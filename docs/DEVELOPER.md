# Developer guide

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [DATAFLOW.md](DATAFLOW.md) first.

## Setup

```bash
uv sync                     # runtime + dev dependencies (pytest, ruff, pylint)
uv venv .venv-pylint --python 3.14 && uv pip install --python .venv-pylint semgrep==1.181.0
```

## Test

```bash
uv run pytest
```

Tests start a throwaway PostgreSQL cluster (no Docker, about 1 s; needs `initdb`, `pg_ctl`,
`psql` on the `PATH`), load IntelMQ's schema and synthetic events, and use a read-only role.
No LLM is needed: the chat loop uses a scripted fake, `ask`/`check` a fake OpenAI-compatible
server. Line and branch coverage is printed after every run; below 95% the run fails.
What runs when, and how: [TESTING.md](TESTING.md). LLM evals: [EVALS.md](EVALS.md).

## Code quality (same as CI, `.github/workflows/quality.yml`)

```bash
uv run ruff format --check src tests evals
uv run ruff check src tests evals
uv run pylint src evals
uv run pylint tests --disable=missing-function-docstring,redefined-outer-name,too-few-public-methods
.venv-pylint/bin/semgrep scan --config p/python --config p/secrets --metrics=off --error src tests evals
uv audit
```

## End-to-end test against a real eventdb and LLM

```bash
uv run eventdb-mcp check
uv run eventdb-mcp ask -v "Which 5 feeds delivered the most events in June 2026?"
tail logs/eventdb-mcp.log           # the SQL that produced the answer
```

Compare the answer with the same SQL run in `psql`.

## Adding a tool

1. Add a function decorated with `@mcp.tool(annotations={"readOnlyHint": True})` inside
   `create_server()` in `server.py`. The docstring and `Field(description=...)` are what the
   LLM sees: keep them short and concrete.
2. Build SQL with `psycopg.sql` (`sql.Identifier` for columns, `%s` parameters for values) and
   run it through the local `run()` helper, which goes through `db.run_query()`.
3. Add tests in `tests/test_server.py` (and update `test_tools_listed`), then accept the new
   contract: `UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py`.
4. Document it in `docs/USER_GUIDE.md` and `docs/llms.txt`.

## Adding a filter

Add one line to `FILTERS` in `server.py` (argument → column, operator), add the argument to
`search_events` and `event_stats`, and add a test.

## Dependencies

Ask before adding a dependency. Versions are pinned with hashes in `uv.lock`; `[tool.uv]
exclude-newer` in `pyproject.toml` keeps packages younger than ~48 h out of resolution. After
changing dependencies, run `uv audit`.

## Schema updates

`src/eventdb_mcp/data/harmonization.json` and `tests/fixtures/initdb.sql` are copied from
IntelMQ (AGPL-3.0-or-later). When IntelMQ adds fields, regenerate both from
`intelmq/etc/harmonization.conf` and `intelmq/tests/bin/initdb.sql`.
`test_harmonization_matches_intelmq_schema` fails if they drift apart.
