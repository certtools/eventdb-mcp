"""End-to-end tests: the real ``eventdb-mcp`` commands in subprocesses.

* ``serve`` over stdio, started exactly like an MCP client (Claude Desktop) does
* ``serve --transport http``, reached over the network
* ``ask`` and ``check`` against a fake OpenAI-compatible LLM server

All of them use the throwaway PostgreSQL cluster and an explicit env file, so
they never touch a developer's real ``.env`` database.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess  # nosec B404 - runs our own CLI
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastmcp import Client

from tests.conftest import ROOT, _free_port

pytestmark = pytest.mark.e2e


@pytest.fixture
def env_file(tmp_path, pg_cluster):
    """Env file for the read-only role of the test cluster; returns (path, env vars)."""
    values = {
        "EVENTDB_DSN": pg_cluster[1],
        "EVENTDB_MCP_LOG_FILE": str(tmp_path / "e2e.log"),
        "LLM_MODEL": "fake-model",
        "LLM_BASE_URL": "http://127.0.0.1:9/v1",
    }
    path = tmp_path / "e2e.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path, values


def clean_env(**extra) -> dict:
    """os.environ without our settings (they would override the env file), plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EVENTDB_", "MCP_", "LLM_"))}
    return env | extra


def cli(*args, env=None, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603  # nosec B603
        [sys.executable, "-m", "eventdb_mcp", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or clean_env(),
        check=False,
    )


@pytest.mark.skipif(not shutil.which("uv"), reason="uv not on PATH")
def test_stdio_server_as_started_by_an_mcp_client(env_file):
    path, values = env_file
    config = {  # same shape as in docs/GETTING_STARTED.md, plus an explicit env file
        "mcpServers": {
            "intelmq-eventdb": {
                "command": "uv",
                "args": ["--directory", str(ROOT), "run", "eventdb-mcp", "--env-file", str(path), "serve"],
                "env": values,
            }
        }
    }

    async def session():
        async with Client(config) as client:
            tools = {t.name for t in await client.list_tools()}
            stats = await client.call_tool("event_stats", {"group_by": "source.geolocation.cc", "limit": 1})
            write = await client.call_tool("run_sql", {"query": "DELETE FROM events"}, raise_on_error=False)
            plot = await client.call_tool("chart", {"group_by": "source.geolocation.cc", "series": 1})
            return tools, stats.structured_content["rows"], write, plot.structured_content["chart"]

    tools, rows, write, chart = asyncio.run(session())
    assert tools == {"list_tables", "describe_table", "search_events", "event_stats", "chart", "run_sql"}
    assert rows == [{"value": "AT", "events": 5}]
    assert write.is_error
    assert [(s["name"], s["total"]) for s in chart["series"]] == [("AT", 5)]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early: {proc.stderr.read()}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"port {port} not open after {timeout}s")


def test_http_server(env_file):
    path, _ = env_file
    port = _free_port()
    proc = subprocess.Popen(  # noqa: S603  # nosec B603  # pylint: disable=consider-using-with  # stopped in finally
        [sys.executable, "-m", "eventdb_mcp", "--env-file", str(path), "serve", "--transport", "http"],
        env=clean_env(MCP_PORT=str(port)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(port, proc)

        async def session():
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                found = await client.call_tool("search_events", {"source_ip": "192.0.2.0/24"})
                write = await client.call_tool(
                    "run_sql", {"query": "UPDATE events SET comment='x'"}, raise_on_error=False
                )
                return found.structured_content, write

        found, write = asyncio.run(session())
        assert found["row_count"] == 3
        assert write.is_error and "Only SELECT" in write.content[0].text
    finally:
        proc.terminate()
        proc.wait(timeout=10)


class FakeLLM(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible server: one tool call, then an answer built from its result."""

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802  # pylint: disable=invalid-name  # http.server API
        self._send(
            {
                "object": "list",
                "data": [{"id": "fake-model", "object": "model", "created": 0, "owned_by": "t"}],
            }
        )

    def do_POST(self):  # noqa: N802  # pylint: disable=invalid-name  # http.server API
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = request["messages"][-1]
        if last["role"] == "tool":
            top = json.loads(last["content"])["rows"][0]
            message = {
                "role": "assistant",
                "content": f"Top country: **{top['value']}** ({top['events']} events)",
            }
        else:
            call = {"name": "event_stats", "arguments": '{"group_by": "source.geolocation.cc", "limit": 1}'}
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": call}],
            }
        self._send(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "fake-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            }
        )


@pytest.fixture
def fake_llm_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_ask_end_to_end(env_file, fake_llm_url):
    path, _ = env_file
    result = cli(
        "--env-file", str(path), "ask", "-v", "Top country?", env=clean_env(LLM_BASE_URL=fake_llm_url)
    )
    assert result.returncode == 0, result.stderr
    assert "Top country: AT (5 events)" in result.stdout
    assert "event_stats(" in result.stderr  # -v shows the tool call
    assert "tool call: event_stats" in (path.parent / "e2e.log").read_text()


def test_check_end_to_end(env_file, fake_llm_url):
    path, _ = env_file
    ok = cli("--env-file", str(path), "check", env=clean_env(LLM_BASE_URL=fake_llm_url))
    assert ok.returncode == 0, ok.stderr
    assert "✓ database" in ok.stderr and "✓ LLM" in ok.stderr
    down = cli("--env-file", str(path), "check")  # LLM_BASE_URL from file: nothing listens on port 9
    assert down.returncode == 1
    assert "✗ LLM" in down.stderr
