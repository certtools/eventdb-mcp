"""Settings, read from environment variables (and a local ``.env`` file).

Every knob lives here so that ``env.example`` and this file are the single
source of truth. Real environment variables win over values in ``.env``.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """All runtime settings. Build it with :meth:`from_env`."""

    # PostgreSQL / eventdb
    db_dsn: str = ""  # empty -> libpq falls back to PGHOST, PGUSER, ... env vars
    db_table: str = "events"
    statement_timeout_ms: int = 30_000
    max_rows: int = 200
    allow_superuser: bool = False

    # FastMCP server
    mcp_transport: str = "stdio"  # stdio | http
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000

    # OpenAI-compatible LLM (e.g. ollama, vLLM, llama.cpp, LM Studio)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3.8:27b-mlx"
    llm_max_steps: int = 8
    llm_timeout_s: float = 300.0

    log_file: str = "logs/eventdb-mcp.log"

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        """Load ``env_file`` (if it exists) into ``os.environ``, then read settings."""
        if env_file and Path(env_file).is_file():
            load_dotenv(env_file, override=False)
        env = os.environ.get
        d = cls()  # defaults
        return cls(
            db_dsn=env("EVENTDB_DSN", d.db_dsn),
            db_table=env("EVENTDB_TABLE", d.db_table),
            statement_timeout_ms=int(env("EVENTDB_STATEMENT_TIMEOUT_MS", d.statement_timeout_ms)),
            max_rows=int(env("EVENTDB_MAX_ROWS", d.max_rows)),
            allow_superuser=_bool(env("EVENTDB_ALLOW_SUPERUSER", str(d.allow_superuser))),
            mcp_transport=env("MCP_TRANSPORT", d.mcp_transport),
            mcp_host=env("MCP_HOST", d.mcp_host),
            mcp_port=int(env("MCP_PORT", d.mcp_port)),
            llm_base_url=env("LLM_BASE_URL", d.llm_base_url),
            llm_api_key=env("LLM_API_KEY", d.llm_api_key),
            llm_model=env("LLM_MODEL", d.llm_model),
            llm_max_steps=int(env("LLM_MAX_STEPS", d.llm_max_steps)),
            llm_timeout_s=float(env("LLM_TIMEOUT_S", d.llm_timeout_s)),
            log_file=env("EVENTDB_MCP_LOG_FILE", d.log_file),
        )
