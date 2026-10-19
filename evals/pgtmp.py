"""Throwaway PostgreSQL clusters for tests, evals and benchmarks.

No Docker: ``initdb`` + ``pg_ctl`` start a private cluster in a directory under
``tmp/`` on a random localhost port in about one second. It is stopped and
deleted afterwards.
"""

import shutil
import socket
import subprocess  # nosec B404 - only runs local PostgreSQL tools
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INITDB_SQL = ROOT / "tests" / "fixtures" / "initdb.sql"
READONLY_ROLE = "eventdb_ro"


def available() -> bool:
    """True if the PostgreSQL server binaries are on the PATH."""
    return all(shutil.which(tool) for tool in ("initdb", "pg_ctl", "psql"))


def free_port() -> int:
    """A free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(*cmd: str) -> None:
    """Run a PostgreSQL tool; raise on failure."""
    subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603  # nosec B603


@contextmanager
def cluster(prefix: str) -> Iterator[str]:
    """Start a cluster in a new, unique ``tmp/<prefix>-XXXX`` directory.

    Yields the base DSN ``postgresql://{user}@127.0.0.1:{port}``: format it with
    ``user`` (``postgres`` = superuser, or :data:`READONLY_ROLE`) and append
    ``/dbname``. Unique directories let tests, evals and benchmarks run concurrently.
    """
    (ROOT / "tmp").mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=ROOT / "tmp"))
    port = free_port()
    run("initdb", "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8")
    run("pg_ctl", "-D", str(data), "-w", "-l", str(data / "server.log"),
        "-o", f"-p {port} -k {data} -c listen_addresses=127.0.0.1", "start")  # fmt: skip
    try:
        base = f"postgresql://{{user}}@127.0.0.1:{port}"
        run("psql", base.format(user="postgres") + "/postgres", "-c", f"CREATE ROLE {READONLY_ROLE} LOGIN")
        yield base
    finally:
        run("pg_ctl", "-D", str(data), "-m", "fast", "stop")
        shutil.rmtree(data, ignore_errors=True)


def create_eventdb(base: str, name: str, *sql_files: Path) -> tuple[str, str]:
    """Create database ``name`` with the IntelMQ schema plus ``sql_files``.

    Returns ``(superuser_dsn, readonly_dsn)``; the read-only role may SELECT all tables.
    """
    run("psql", base.format(user="postgres") + "/postgres", "-c", f"CREATE DATABASE {name}")
    su_dsn = base.format(user="postgres") + f"/{name}"
    for sql_file in (INITDB_SQL, *sql_files):
        run("psql", su_dsn, "-v", "ON_ERROR_STOP=1", "-f", str(sql_file))
    run("psql", su_dsn, "-c", f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY_ROLE}")
    return su_dsn, base.format(user=READONLY_ROLE) + f"/{name}"
