# Changelog

## Unreleased

- New: read-only tool `chart`: events per hour or day (time.source, UTC), one line per top
  value of a whitelisted column (feed, classification, malware, ASN, country, ...), same
  filters as `search_events`. Returns the chart spec of clickhouse-mcp's `chart`
  (`{chart: {type: "line", title, x, y, series}, window}`), which chat-ui draws as SVG. At
  most 1,000 points; empty buckets filled with 0; default window = 30 days up to the latest
  matching event. Tests: exact SQL and parameters, refusals, e2e over stdio.

- New: `python -m evals.export --rows N --out DIR` writes the golden eventdb as `golden.sql`
  (schema + COPY data, loadable by plain psql or the postgres image's init directory) and
  `answers.json` (prompts + expected answers). Used by chat-ui's Docker test stack. Tested by
  loading the export into a throwaway cluster and checking all 30 reference queries.

- New: golden-question LLM eval (`python -m evals.run`): deterministic synthetic eventdb
  (1,000 rows by default, 1,000,000 for the benchmark), 30 questions with answers computed
  in Python and verified against reference SQL, grading via `ANSWER:` line; metrics:
  accuracy per category/kind, set precision/recall/F1 (macro/micro), yes/no confusion
  matrix, numeric error, tool usage, latency, safety (write attempts, prompt-injection
  canary), pass@k / pass^k. Reports in `logs/evals/`.
- New: end-to-end tests that run `serve` (stdio and HTTP), `ask` and `check` in
  subprocesses (fake OpenAI-compatible server for the LLM); contract snapshot of the MCP
  tools; tests for every column type (jsonb, real, boolean, inet v4/v6, timestamptz, enum).
- Changed: strict `.env` parsing (pydantic): malformed or out-of-range values and unknown
  `EVENTDB_*`/`MCP_*`/`LLM_*` variables stop the CLI with one clear message (exit code 2).
- Fix: numeric aggregates (`avg()`, `sum()`) are returned as numbers, not strings.
- Fix: timestamps are always UTC (DB session pinned to `TimeZone=UTC`); they used to follow
  the server's local time zone.
- Tests: throwaway PostgreSQL clusters get unique directories (concurrent runs no longer
  delete each other's database); coverage now includes branches and `evals/`, minimum 95%.
- CI: uses the PostgreSQL binaries preinstalled on GitHub runners (no Docker); lint covers
  `evals/`; new release-only benchmark job (tags `v*` or manual).
- Docs: `docs/TESTING.md`, `docs/EVALS.md`; developer, admin, architecture, data-flow guides
  updated.

## 0.1.0 - 2026-10-19

- New: FastMCP server for the IntelMQ eventdb with read-only tools `list_tables`,
  `describe_table`, `search_events`, `event_stats`, `run_sql`.
- New: `eventdb-mcp ask` answers questions with any OpenAI-compatible LLM (configured via
  `LLM_*` in `.env`), using the MCP tools; `eventdb-mcp check` verifies the setup;
  `eventdb-mcp serve` runs the server over stdio or HTTP (`MCP_*` in `.env`).
- Security: single-SELECT guard, read-only transaction locked by an early snapshot,
  statement timeout, superuser refusal (`EVENTDB_ALLOW_SUPERUSER`), row cap, audit log.
- Supply chain: dependencies pinned with hashes in `uv.lock`; `exclude-newer` cutoff;
  fastmcp pinned to 4.1.0 (4.1.1 was < 48 h old). `uv audit`: no known vulnerabilities.
- Tests: throwaway PostgreSQL cluster with IntelMQ schema, fake LLM; CI workflow with ruff,
  pylint, semgrep, pytest, uv audit. Docs for users, admins, developers and agents.
- Fix: `ask` prints a short error (exit 1) instead of a traceback when the LLM server is
  unreachable or fails.
- Prompt: server instructions warn against summing per-group `COUNT(DISTINCT ...)` values
  (seen in an end-to-end run: 1,275 reported vs. 262 actual distinct IPs).
- Verified end-to-end against a real eventdb (8.9M events) with ollama and vLLM (qwen3.8).
