# User guide

## Commands

| Command | What it does |
|---------|--------------|
| `eventdb-mcp ask "QUESTION"` | Answer a question with the local LLM. Add `-v` to see the tool calls. |
| `eventdb-mcp check` | Test the database and LLM settings. Exit code 0 = all good. |
| `eventdb-mcp serve` | Run the MCP server for other MCP clients (see [Admin guide](ADMIN_GUIDE.md)). |
| `eventdb-mcp --env-file other.env ...` | Use another settings file, e.g. for a second eventdb. |

## Asking good questions

The LLM knows the IntelMQ field names and today's date. It works best when you:

- **Give a time range.** The eventdb can hold many millions of rows. "in June 2026" or "in the
  last 7 days" keeps queries fast.
- **Use IntelMQ terms** where you know them: feed names, `classification.type` values like
  `infected-system`, `phishing`, `scanner`, `vulnerable-system`, country codes like `AT`.
- **Ask for a number of results**: "top 10", "the 20 newest".

Examples:

```bash
uv run eventdb-mcp ask "How many events per classification.taxonomy were there in June 2026?"
uv run eventdb-mcp ask "Show the 10 newest phishing events with their URLs"
uv run eventdb-mcp ask "Everything we know about 192.0.2.10 since 2026-01-01"
uv run eventdb-mcp ask "Which ASNs in AT had the most infected systems in June 2026?"
uv run eventdb-mcp ask "Plot events per day by classification.type in March 2026"
```

Questions with "plot", "chart", "trend", "over time", "per day" or "per hour" go to the
`chart` tool. A UI that knows the chart format (chat-ui) draws it; in `ask` the LLM
describes it from each line's total and peak.

## Tools the LLM can use

| Tool | Use it for |
|------|------------|
| `list_tables` | Which tables/views exist, roughly how many rows |
| `describe_table` | Columns, SQL types and the IntelMQ description of each field |
| `search_events` | Events matching filters (IP or CIDR, ASN, FQDN, country, classification, feed, malware, time range), newest first |
| `event_stats` | Top-N counts grouped by any column |
| `chart` | Events per hour or day (by `time.source`, UTC), one line per top value of a column such as `feed.name`, `classification.type` or `source.geolocation.cc`; same filters as `search_events` |
| `run_sql` | Any other read-only `SELECT` |

## Limits you will notice

- Results are capped at `EVENTDB_MAX_ROWS` rows (default 200) per query. Ask for aggregates
  instead of raw rows when you need the big picture.
- Queries are aborted after `EVENTDB_STATEMENT_TIMEOUT_MS` (default 30 s). Narrow the time range.
- A chart has at most 1,000 points (lines x time buckets): 6 lines fit 166 days, 1 line
  1,000 days; hourly buckets are used when they fit, else daily. Without a time range a
  chart shows the 30 days up to the latest matching event. Charts can group only by
  categorical columns (feed, classification, malware, ASN, country, protocol, port, severity,
  TLP), not by IP or URL.
- The LLM stops after `LLM_MAX_STEPS` tool calls (default 8) and says so.
- **Verify important numbers.** Run `ask -v` to see the exact query, or check the audit log
  (`logs/eventdb-mcp.log`). LLMs can misread results. Totals the LLM computes itself
  (e.g. by adding up per-country counts) are the most common error source.
- Event fields such as `raw`, `comment` or `extra` come from external feeds. The LLM is told to
  treat them as data, but be suspicious if an answer contains odd instructions or links.
