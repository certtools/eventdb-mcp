"""Settings, read from environment variables (and a local ``.env`` file).

Every knob lives here so that ``env.example`` and this file are the single
source of truth. Real environment variables win over values in ``.env``.

Parsing is strict: a malformed value (``EVENTDB_MAX_ROWS=abc``) or an unknown
variable with one of our prefixes in ``.env`` (typo such as ``LLM_MODLE``) is
a :class:`ConfigError` that names every offending variable.
"""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PREFIXES = ("EVENTDB_", "MCP_", "LLM_")


class ConfigError(Exception):
    """Invalid configuration. The message lists every problem."""


class Settings(BaseModel):
    """All runtime settings. Build it with :meth:`from_env`; field alias = env variable."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    # PostgreSQL / eventdb. Empty DSN -> libpq falls back to PGHOST, PGUSER, ... env vars.
    db_dsn: str = Field("", alias="EVENTDB_DSN")
    db_table: str = Field("events", alias="EVENTDB_TABLE", min_length=1)
    statement_timeout_ms: int = Field(30_000, alias="EVENTDB_STATEMENT_TIMEOUT_MS", gt=0)
    max_rows: int = Field(200, alias="EVENTDB_MAX_ROWS", gt=0, le=100_000)
    allow_superuser: bool = Field(False, alias="EVENTDB_ALLOW_SUPERUSER")

    # FastMCP server
    mcp_transport: Literal["stdio", "http"] = Field("stdio", alias="MCP_TRANSPORT")
    mcp_host: str = Field("127.0.0.1", alias="MCP_HOST", min_length=1)
    mcp_port: int = Field(8000, alias="MCP_PORT", gt=0, lt=65536)

    # OpenAI-compatible LLM (e.g. ollama, vLLM, llama.cpp, LM Studio)
    llm_base_url: str = Field("http://localhost:11434/v1", alias="LLM_BASE_URL", pattern=r"^https?://")
    llm_api_key: str = Field("ollama", alias="LLM_API_KEY")
    llm_model: str = Field("qwen3.8:27b-mlx", alias="LLM_MODEL", min_length=1)
    llm_max_steps: int = Field(8, alias="LLM_MAX_STEPS", gt=0, le=100)
    llm_timeout_s: float = Field(300.0, alias="LLM_TIMEOUT_S", gt=0)

    log_file: str = Field("logs/eventdb-mcp.log", alias="EVENTDB_MCP_LOG_FILE", min_length=1)

    @classmethod
    def env_names(cls) -> set[str]:
        """All environment variable names we understand."""
        return {f.alias for f in cls.model_fields.values() if f.alias}

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        """Read ``env_file`` (if it exists) and the environment; raise :class:`ConfigError`."""
        known = cls.env_names()
        file_values: dict[str, str] = {}
        if env_file and Path(env_file).is_file():
            file_values = {k: v or "" for k, v in dotenv_values(env_file).items()}
        unknown = sorted(k for k in file_values if k.startswith(PREFIXES) and k not in known)
        if unknown:
            raise ConfigError(f"Unknown variable(s) in {env_file}: {', '.join(unknown)} (typo?)")
        values = {k: v for k, v in file_values.items() if k in known}
        values |= {k: os.environ[k] for k in known if k in os.environ}  # real env wins
        try:
            settings = cls.model_validate(values)
        except ValidationError as exc:
            problems = [f"{err['loc'][0]}={err.get('input')!r}: {err['msg']}" for err in exc.errors()]
            raise ConfigError("Invalid configuration:\n  " + "\n  ".join(problems)) from None
        for key, value in file_values.items():  # export other vars, e.g. PGPASSWORD, for libpq
            if key not in known:
                os.environ.setdefault(key, value)
        return settings
