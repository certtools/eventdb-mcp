# Changelog

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
