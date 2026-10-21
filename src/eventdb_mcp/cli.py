"""Command line interface: ``eventdb-mcp serve | ask | check``."""

import asyncio
import logging
from pathlib import Path

import click
import openai
from psycopg import sql
from rich.console import Console
from rich.markdown import Markdown

from eventdb_mcp import db
from eventdb_mcp.chat import ask as ask_llm
from eventdb_mcp.config import ConfigError, Settings
from eventdb_mcp.server import create_server

# stderr only: with the stdio transport, stdout belongs to the MCP protocol.
console = Console(stderr=True)


def _setup(env_file: str) -> Settings:
    try:
        settings = Settings.from_env(env_file)
    except ConfigError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(2) from exc
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("eventdb_mcp")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return settings


@click.group()
@click.option("--env-file", default=".env", show_default=True, help="Settings file.")
@click.pass_context
def main(ctx: click.Context, env_file: str) -> None:
    """MCP server and LLM chat client for the IntelMQ eventdb."""
    ctx.obj = _setup(env_file)


@main.command()
@click.option("--transport", type=click.Choice(["stdio", "http"]), help="Overrides MCP_TRANSPORT.")
@click.pass_obj
def serve(settings: Settings, transport: str | None) -> None:
    """Run the MCP server (stdio for desktop clients, http for the network)."""
    server = create_server(settings)
    transport = transport or settings.mcp_transport
    if transport == "http":
        server.run(transport="http", host=settings.mcp_host, port=settings.mcp_port)
    else:
        server.run(transport="stdio", show_banner=False)


@main.command()
@click.argument("question")
@click.option("-v", "--verbose", is_flag=True, help="Show the tool calls the LLM makes.")
@click.pass_obj
def ask(settings: Settings, question: str, verbose: bool) -> None:
    """Ask QUESTION in plain language; the local LLM queries the eventdb."""

    def show(name: str, arguments: str) -> None:
        if verbose:
            console.print(f"[dim]→ {name}({arguments})[/dim]")

    try:
        with console.status(f"Asking {settings.llm_model} …"):
            answer = asyncio.run(ask_llm(question, settings, create_server(settings), on_tool_call=show))
    except openai.APIError as exc:
        logging.getLogger("eventdb_mcp").error("LLM error: %s", exc)
        console.print(f"[red]✗ LLM error[/red] ({settings.llm_base_url}): {exc}")
        raise SystemExit(1) from exc
    Console().print(Markdown(answer))


@main.command()
@click.pass_obj
def check(settings: Settings) -> None:
    """Verify the database and LLM settings in .env."""
    ok = True
    try:
        # max() on the indexed time column is fast even on very large tables.
        query = sql.SQL('SELECT max("time.source") AS latest FROM {}').format(
            sql.Identifier(settings.db_table)
        )
        result = db.run_query(settings, query)
        latest = result["rows"][0]["latest"]
        console.print(f"[green]✓ database[/green] table {settings.db_table}, latest event: {latest}")
    except db.QueryError as exc:
        ok = False
        console.print(f"[red]✗ database[/red] {exc}")
    try:
        llm = openai.OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=30)
        models = [m.id for m in llm.models.list()]
        mark = "[green]✓" if settings.llm_model in models else "[yellow]?"
        console.print(f"{mark} LLM[/] {settings.llm_base_url} model={settings.llm_model} available={models}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        ok = False
        console.print(f"[red]✗ LLM[/red] {settings.llm_base_url}: {exc}")
    raise SystemExit(0 if ok else 1)
