import asyncio
import os

import asyncpg
import pytest
from sqlalchemy import event, text
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db import create_database


def connection_parameters(host, port):
    """Inspect the real dialect's outgoing arguments without contacting a DB."""
    settings = get_settings().model_copy(update={"db_host": host, "db_port": port})
    engine, _ = create_database(settings)
    captured = {}

    class ConnectionProbe(Exception):
        pass

    def probe(dialect, connection_record, args, parameters):
        captured.update(parameters)
        raise ConnectionProbe()

    event.listen(engine.sync_engine, "do_connect", probe)

    async def inspect_connection():
        try:
            with pytest.raises(ConnectionProbe):
                async with engine.connect():
                    pass
        finally:
            await engine.dispose()

    asyncio.run(inspect_connection())
    return captured, engine.sync_engine.pool


@pytest.mark.parametrize("host", [
    "aws-1-ap-southeast-2.pooler.supabase.com",
    "AWS-1-AP-SOUTHEAST-2.POOLER.SUPABASE.COM.",
])
def test_shared_transaction_pooler_avoids_named_statement_collisions(host):
    parameters, pool = connection_parameters(host, 6543)
    assert isinstance(pool, NullPool)
    assert parameters["ssl"] == "require"
    assert parameters["statement_cache_size"] == 0
    assert parameters["prepared_statement_cache_size"] == 0
    name = parameters["prepared_statement_name_func"]
    assert [name() for _ in range(3)] == ["", "", ""]


@pytest.mark.parametrize(("host", "port"), [
    ("aws-1-ap-southeast-2.pooler.supabase.com", 5432),
    ("project-ref.supabase.co", 5432),
])
def test_other_supabase_connections_require_tls_without_transaction_workaround(host, port):
    parameters, pool = connection_parameters(host, port)
    assert isinstance(pool, NullPool)
    assert parameters["ssl"] == "require"
    assert "prepared_statement_name_func" not in parameters
    assert "prepared_statement_cache_size" not in parameters
    assert "statement_cache_size" not in parameters


@pytest.mark.parametrize(("host", "port"), [
    ("db", 5432), ("localhost", 6543), ("pooler.supabase.com.example.org", 6543),
])
def test_local_and_unrelated_hosts_keep_existing_connection_behavior(host, port):
    parameters, pool = connection_parameters(host, port)
    assert not isinstance(pool, NullPool)
    assert "ssl" not in parameters
    assert "prepared_statement_name_func" not in parameters
    assert "prepared_statement_cache_size" not in parameters
    assert "statement_cache_size" not in parameters


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Set TEST_POSTGRES_URL for PostgreSQL integration")
def test_queries_work_when_backend_already_has_another_clients_statement(monkeypatch):
    """Reproduce a pooled backend carrying a statement from an earlier client."""
    original_connect = asyncpg.connect

    async def connect_to_test_backend(*args, **parameters):
        connection = await original_connect(
            os.environ["TEST_POSTGRES_URL"],
            statement_cache_size=parameters.get("statement_cache_size", 100),
        )
        await connection.execute('PREPARE "__asyncpg_stmt_1__" AS SELECT 123')
        return connection

    monkeypatch.setattr(asyncpg, "connect", connect_to_test_backend)

    async def scenario():
        settings = get_settings().model_copy(update={
            "db_host": "test.pooler.supabase.com", "db_port": 6543,
        })
        # New engines also exercise dialect startup queries, where the reported
        # DuplicatePreparedStatementError occurs before the first API request.
        for _ in range(2):
            engine, _ = create_database(settings)
            try:
                async with engine.begin() as connection:
                    for value in range(3):
                        assert await connection.scalar(text("SELECT CAST(:value AS INTEGER)"), {"value": value}) == value
                    names = list((await connection.scalars(text("SELECT name FROM pg_prepared_statements"))).all())
                    assert names == ["__asyncpg_stmt_1__"]
                    assert await connection.scalar(text('EXECUTE "__asyncpg_stmt_1__"')) == 123
            finally:
                await engine.dispose()

    asyncio.run(scenario())
