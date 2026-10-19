"""Shared fixtures: a throwaway PostgreSQL cluster with the IntelMQ schema.

The cluster lives in ``tmp/pytest-pg`` (inside the repo, git-ignored), listens
on a random localhost port and is removed after the test session. Tests that
need it are skipped if ``initdb``/``pg_ctl`` are not on the PATH.
"""

import shutil
import socket
import subprocess  # nosec B404 - only runs local PostgreSQL tools
from dataclasses import replace
from pathlib import Path

import pytest

from eventdb_mcp.config import Settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603  # nosec B603


@pytest.fixture(scope="session")
def pg_cluster():
    """Start a temporary cluster; yield ``(superuser_dsn, readonly_dsn)``."""
    if not (shutil.which("initdb") and shutil.which("pg_ctl") and shutil.which("psql")):
        pytest.skip("PostgreSQL server binaries (initdb, pg_ctl, psql) not found")
    data = ROOT / "tmp" / "pytest-pg"
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    _run("initdb", "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8")
    _run(
        "pg_ctl",
        "-D",
        str(data),
        "-w",
        "-l",
        str(data / "server.log"),
        "-o",
        f"-p {port} -k {data} -c listen_addresses=127.0.0.1",
        "start",
    )
    try:
        admin = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        _run("psql", admin, "-c", "CREATE DATABASE eventdb")
        su_dsn = f"postgresql://postgres@127.0.0.1:{port}/eventdb"
        for sql_file in ("initdb.sql", "sample_events.sql"):
            _run("psql", su_dsn, "-v", "ON_ERROR_STOP=1", "-f", str(FIXTURES / sql_file))
        _run(
            "psql",
            su_dsn,
            "-c",
            "CREATE ROLE eventdb_ro LOGIN; GRANT SELECT ON ALL TABLES IN SCHEMA public TO eventdb_ro;",
        )
        yield su_dsn, f"postgresql://eventdb_ro@127.0.0.1:{port}/eventdb"
    finally:
        _run("pg_ctl", "-D", str(data), "-m", "fast", "stop")
        shutil.rmtree(data, ignore_errors=True)


@pytest.fixture
def settings(pg_cluster) -> Settings:
    """Settings pointing at the read-only role of the test cluster."""
    return replace(Settings(), db_dsn=pg_cluster[1], max_rows=5, log_file="tmp/test.log")


@pytest.fixture
def su_settings(pg_cluster) -> Settings:
    """Settings using the superuser of the test cluster."""
    return replace(Settings(), db_dsn=pg_cluster[0], log_file="tmp/test.log")
