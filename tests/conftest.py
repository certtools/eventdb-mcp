"""Shared fixtures: a throwaway PostgreSQL cluster with the IntelMQ schema.

The cluster lives in a unique ``tmp/pytest-pg-*`` directory (git-ignored), listens
on a random localhost port and is removed after the test session (see
``evals/pgtmp.py``). Tests that need it are skipped if ``initdb``/``pg_ctl``
are not on the PATH.
"""

from pathlib import Path

import pytest

from evals import pgtmp
from eventdb_mcp.config import Settings

ROOT = pgtmp.ROOT
FIXTURES = Path(__file__).parent / "fixtures"
_free_port = pgtmp.free_port


@pytest.fixture(scope="session")
def pg_base():
    """Base DSN of the session cluster, ``postgresql://{user}@127.0.0.1:PORT``."""
    if not pgtmp.available():
        pytest.skip("PostgreSQL server binaries (initdb, pg_ctl, psql) not found")
    with pgtmp.cluster("pytest-pg") as base:
        yield base


@pytest.fixture(scope="session")
def pg_cluster(pg_base):
    """Database "eventdb" with 9 sample events; ``(superuser_dsn, readonly_dsn)``."""
    return pgtmp.create_eventdb(pg_base, "eventdb", FIXTURES / "sample_events.sql")


@pytest.fixture(scope="session")
def typed_dsn(pg_base):
    """Database "typed" with rows covering every column type (read-only DSN)."""
    return pgtmp.create_eventdb(pg_base, "typed", FIXTURES / "typed_events.sql")[1]


@pytest.fixture
def settings(pg_cluster) -> Settings:
    """Settings pointing at the read-only role of the test cluster."""
    return Settings().model_copy(update={"db_dsn": pg_cluster[1], "max_rows": 5, "log_file": "tmp/test.log"})


@pytest.fixture
def su_settings(pg_cluster) -> Settings:
    """Settings using the superuser of the test cluster."""
    return Settings().model_copy(update={"db_dsn": pg_cluster[0], "log_file": "tmp/test.log"})


@pytest.fixture
def typed_settings(typed_dsn) -> Settings:
    """Settings for the "typed" database (see fixtures/typed_events.sql)."""
    return Settings().model_copy(update={"db_dsn": typed_dsn, "log_file": "tmp/test.log"})
