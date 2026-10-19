# eventdb-mcp

Ask your [IntelMQ](https://github.com/certtools/intelmq) eventdb questions in plain language, with a **local** LLM.

```text
$ eventdb-mcp ask "Which 5 feeds delivered the most events in June 2026?"
 Rank  Feed                     Events
 1     Drop                     585,258
 2     Open-MongoDB             425
 ...
```

`eventdb-mcp` is two things:

1. **An MCP server** (built with [FastMCP](https://gofastmcp.com)) that gives any MCP client
   (Claude Desktop, IDEs, agents) safe, read-only tools for the IntelMQ eventdb (PostgreSQL).
2. **A small chat client** that connects any OpenAI-compatible LLM (ollama, vLLM,
   llama.cpp, LM Studio, ...) to those tools. Your event data never leaves your network.

Why: the eventdb holds millions of rows with 90 dotted column names (`"source.ip"`,
`"classification.type"`, ...). Analysts know what they want to ask, not always how to write
the SQL. The LLM writes the queries; the server makes sure they can only read.

## Quick start

```bash
uv sync
cp env.example .env        # then set EVENTDB_DSN and LLM_* in .env
uv run eventdb-mcp check   # verifies database + LLM
uv run eventdb-mcp ask -v "Top 10 ASNs with infected systems last week?"
```

## Documentation

| You want to ...                     | Read                                           |
|-------------------------------------|------------------------------------------------|
| try it out                          | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| use it day to day, write good questions | [docs/USER_GUIDE.md](docs/USER_GUIDE.md)   |
| install, secure and operate it      | [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)     |
| change or extend the code           | [docs/DEVELOPER.md](docs/DEVELOPER.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DATAFLOW.md](docs/DATAFLOW.md) |
| see how it is tested and evaluated  | [docs/TESTING.md](docs/TESTING.md), [docs/EVALS.md](docs/EVALS.md) |
| integrate it from an agent          | [docs/llms.txt](docs/llms.txt)                 |

Changes: [CHANGELOG.md](CHANGELOG.md).
