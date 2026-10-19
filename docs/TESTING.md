# Testing

How eventdb-mcp is tested, what runs when, and how to run it yourself.

## Summary

| Layer | What | Runs | Time |
|-------|------|------|------|
| Unit | SQL guard, WHERE builder, strict config, grader, metrics, dataset quotas | every commit | < 1 s |
| Integration | query runner, all 5 MCP tools, every column type, read-only enforcement | every commit | ~5 s |
| Contract | snapshot of tool names, descriptions, input schemas | every commit | < 1 s |
| End-to-end | `serve` over stdio and HTTP, `ask`, `check` in subprocesses | every commit | ~6 s |
| Golden answer key | 1,000-row golden DB: Python answers == reference SQL for all 30 questions | every commit | ~1 s |
| Doc check | `env.example` documents and parses every setting | every commit | < 1 s |
| LLM eval | 30 golden questions asked to a real LLM, full metrics | before a release, or when the model or prompt changes | ~10-30 min |
| Benchmark | 1,000,000-row golden DB: answer key vs. SQL, load rate, query timings | release tags (`v*`) or manual | ~25 s (1) |

(1) Measured on an Apple Silicon Mac, 2026-10-19: generate + answer key 9 s, `COPY` at
112,000 rows/s, slowest reference query 0.28 s, peak memory 530 MB.

Everything except the LLM eval runs with `uv run pytest` (about 20 s) and in CI
(`.github/workflows/quality.yml`).

## The throwaway PostgreSQL

Tests do not use Docker. `evals/pgtmp.py` runs `initdb` + `pg_ctl` to start a
private cluster in a unique `tmp/<name>-XXXX/` directory on a random localhost
port. That takes about one second. The cluster is deleted afterwards. Unique
directories let pytest, evals and benchmarks run at the same time.

- Locally you need the PostgreSQL server binaries (`initdb`, `pg_ctl`, `psql`) on the `PATH`.
  Without them the database tests are skipped.
- GitHub's Ubuntu runners have them preinstalled under `/usr/lib/postgresql/*/bin`; CI
  only adds that directory to the `PATH`.

Databases in the test cluster:

| Database | Content | Used by |
|----------|---------|---------|
| `eventdb` | 9 sample events (`tests/fixtures/sample_events.sql`) | tools, CLI, e2e |
| `typed` | 3 rows covering every column type (`tests/fixtures/typed_events.sql`) | `tests/test_types.py` |
| `golden` | 1,000 golden events (`evals/dataset.py`) | `tests/test_evals.py` |

All test data is synthetic: documentation IPs (RFC 5737/3849), ASNs (RFC 5398),
example.* domains.

## Run

```bash
uv run pytest                              # everything except the real-LLM eval
uv run pytest -m e2e                       # only the subprocess end-to-end tests
uv run pytest tests/test_types.py          # only the column-type tests
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py   # accept a tool contract change
uv run python -m evals.run --no-llm        # build the golden DB, verify the answer key
uv run python -m evals.run                 # + ask the LLM from .env all 30 questions
uv run python -m evals.run --rows 1000000 --no-llm        # release benchmark
LLM_EVAL=1 uv run pytest -m llm_eval       # LLM eval as a pass/fail test (EVAL_MIN_ACCURACY=0.8)
```

Coverage (line + branch) is printed after every pytest run. The run fails below 95%.

## What each area covers

**Read-only enforcement** (`tests/test_db.py`): only a single `SELECT`/`WITH`; data-modifying
CTEs, `set_config('transaction_read_only', 'off')` and `pg_sleep` past the timeout fail even
as a superuser; superusers are refused by default; `pg_read_file` is denied for the read-only role.

**Column types** (`tests/test_types.py`): how each type reaches MCP clients and the LLM:

| PostgreSQL type | Column(s) | JSON result |
|-----------------|-----------|-------------|
| `jsonb` | `extra`, `output` | object / array (nested, unicode), `null` |
| `jsonb` operators | `extra->>'attempts'`, `@>` | string / int / filter |
| `boolean` | `source.tor_node` | `true` / `false` / `null` |
| `real` | `feed.accuracy` | number (float4 precision: 0.1 → 0.10000000149) |
| `numeric` aggregates | `avg()`, `sum()` | number (int if integral) |
| `inet` | `source.ip`, `source.network` | string, IPv4 and IPv6, with prefix |
| `timestamptz` | `time.source` | ISO string in UTC (the session is pinned to UTC) |
| `severity_enum` | `severity` | string; `ORDER BY` follows severity order |

**Strict configuration** (`tests/test_config_cli.py`): malformed or out-of-range values and
unknown `EVENTDB_*`/`MCP_*`/`LLM_*` variables (typos) stop the CLI with exit code 2 and one
message listing every problem.

**Contract** (`tests/test_contract.py`): a fastmcp or pydantic upgrade that changes what
clients see (as `inputSchema` → `input_schema` did) fails this test.

**End-to-end** (`tests/test_e2e.py`): the stdio server is started from the same MCP client
config as in [Getting started](GETTING_STARTED.md); the HTTP server on a free port; `ask`
and `check` talk to a fake OpenAI-compatible HTTP server. Every subprocess gets an explicit
env file, so a developer's real `.env` database is never touched.

**Evals**: see [EVALS.md](EVALS.md).

## Not covered (on purpose)

- The `v_events` view of IntelMQ's `--separate-raws` setup.
- Detecting prompt injection by content. The design limits what a successful injection can do
  instead (read-only, no outbound actions); the eval measures whether the model follows
  injected text.
