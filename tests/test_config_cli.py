"""Tests for settings loading and the click CLI."""

import httpx2
import openai
from click.testing import CliRunner

from eventdb_mcp import cli
from eventdb_mcp.config import Settings
from tests.conftest import ROOT

ENV_KEYS = [
    "EVENTDB_DSN",
    "EVENTDB_TABLE",
    "EVENTDB_STATEMENT_TIMEOUT_MS",
    "EVENTDB_MAX_ROWS",
    "EVENTDB_ALLOW_SUPERUSER",
    "MCP_TRANSPORT",
    "MCP_HOST",
    "MCP_PORT",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MAX_STEPS",
    "LLM_TIMEOUT_S",
    "EVENTDB_MCP_LOG_FILE",
]


def test_env_example_documents_every_setting():
    example = (ROOT / "env.example").read_text()
    for key in ENV_KEYS:
        assert f"{key}=" in example, key


def test_settings_from_env_file(tmp_path, monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "EVENTDB_DSN=postgresql://u@h/db\nEVENTDB_ALLOW_SUPERUSER=yes\nMCP_PORT=9000\n"
        "LLM_MODEL=m\nEVENTDB_MAX_ROWS=7\n"
    )
    monkeypatch.setenv("LLM_MODEL", "from-real-env")  # real env vars win
    s = Settings.from_env(env)
    assert (s.db_dsn, s.allow_superuser, s.mcp_port, s.max_rows) == ("postgresql://u@h/db", True, 9000, 7)
    assert s.llm_model == "from-real-env"
    assert Settings.from_env(tmp_path / "missing.env").db_table == "events"


def _invoke(monkeypatch, tmp_path, dsn, *args):
    env = tmp_path / ".env"
    env.write_text(
        f"EVENTDB_DSN={dsn}\nLLM_BASE_URL=http://127.0.0.1:9/v1\nEVENTDB_MCP_LOG_FILE={tmp_path}/log/x.log\n"
    )
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return CliRunner().invoke(cli.main, ["--env-file", str(env), *args])


def test_check_reports_db_ok_and_llm_down(monkeypatch, tmp_path, pg_cluster):
    result = _invoke(monkeypatch, tmp_path, pg_cluster[1], "check")
    assert result.exit_code == 1
    assert "✓ database" in result.output
    assert "✗ LLM" in result.output
    assert (tmp_path / "log" / "x.log").exists()


def test_check_reports_db_down(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, "postgresql://nobody@127.0.0.1:9/none", "check")
    assert result.exit_code == 1
    assert "✗ database" in result.output


def test_ask_prints_answer(monkeypatch, tmp_path):
    async def fake_ask(question, _settings, _server, on_tool_call):
        on_tool_call("list_tables", "{}")
        return f"**answer** to {question}"

    monkeypatch.setattr(cli, "ask_llm", fake_ask)
    result = _invoke(monkeypatch, tmp_path, "postgresql://x", "ask", "-v", "hello?")
    assert result.exit_code == 0, result.output
    assert "answer to hello?" in result.output
    assert "list_tables({})" in result.output


def test_serve_picks_transport(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr("fastmcp.FastMCP.run", lambda self, **kw: runs.append(kw))
    _invoke(monkeypatch, tmp_path, "postgresql://x", "serve")
    _invoke(monkeypatch, tmp_path, "postgresql://x", "serve", "--transport", "http")
    assert runs == [
        {"transport": "stdio", "show_banner": False},
        {"transport": "http", "host": "127.0.0.1", "port": 8000},
    ]


def test_ask_reports_llm_errors_cleanly(monkeypatch, tmp_path):
    async def failing_ask(*_args, **_kwargs):
        raise openai.APIConnectionError(request=httpx2.Request("POST", "http://127.0.0.1:9/v1"))

    monkeypatch.setattr(cli, "ask_llm", failing_ask)
    result = _invoke(monkeypatch, tmp_path, "postgresql://x", "ask", "hello?")
    assert result.exit_code == 1
    assert "✗ LLM error" in result.output
    assert "Traceback" not in result.output
