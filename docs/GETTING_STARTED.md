# Getting started

Ten minutes from clone to your first answer.

## What you need

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- Read access to an IntelMQ eventdb (PostgreSQL with the `events` table created by
  [`intelmq_psql_initdb`](https://github.com/certtools/intelmq/blob/develop/intelmq/bin/intelmq_psql_initdb.py))
- An OpenAI-compatible LLM server **with tool-calling support**, e.g. [ollama](https://ollama.com)

## 1. Install

```bash
git clone <this repository> eventdb-mcp
cd eventdb-mcp
uv sync
```

## 2. Configure

```bash
cp env.example .env
```

Edit `.env`. The three settings you almost always change:

```ini
EVENTDB_DSN=postgresql://eventdb_ro@localhost:5432/intelmq-events
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3.8:27b-mlx
```

Every setting is explained in [`env.example`](../env.example).
`.env` is git-ignored: never commit it.

## 3. Check the setup

```bash
uv run eventdb-mcp check
```

Expected output:

```text
✓ database table events, latest event: 2026-07-01 18:10:32+00:00
✓ LLM http://localhost:11434/v1 model=qwen3.8:27b-mlx available=['qwen3.8:27b-mlx']
```

If a line shows `✗`, see [Troubleshooting](ADMIN_GUIDE.md#troubleshooting).

## 4. Ask a question

```bash
uv run eventdb-mcp ask -v "Which 5 feeds delivered the most events in June 2026?"
```

`-v` prints the tool calls the LLM makes, so you can see which query answered your question.

## 5. Optional: use it from Claude Desktop or another MCP client

Add this to the client's MCP configuration (for Claude Desktop: `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "intelmq-eventdb": {
      "command": "uv",
      "args": ["--directory", "/path/to/eventdb-mcp", "run", "eventdb-mcp", "serve"]
    }
  }
}
```

The server reads its settings from `/path/to/eventdb-mcp/.env`.

Next: [User guide](USER_GUIDE.md).
