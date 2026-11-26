from __future__ import annotations

from sqlalchemy import String, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import Settings
from app.core.limits import MAX_REGION_NAME_LENGTH
from app.models import Base


AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database(
    settings: Settings,
) -> tuple[AsyncEngine, AsyncSessionFactory]:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, session_factory


_SCHEMA_ADVISORY_LOCK_ID = 7_619_883_523_247_101


def _expand_region_name_column(connection: Connection) -> None:
    """Widen an existing bounded column without shrinking it or changing data."""
    columns = inspect(connection).get_columns("recommendations")
    region_type = next(column["type"] for column in columns if column["name"] == "region_name")
    if (
        isinstance(region_type, String)
        and region_type.length is not None
        and region_type.length < MAX_REGION_NAME_LENGTH
    ):
        connection.execute(text(
            "ALTER TABLE recommendations ALTER COLUMN region_name "
            f"TYPE VARCHAR({MAX_REGION_NAME_LENGTH})"
        ))


async def initialize_database(engine: AsyncEngine) -> None:
    """Create tables and expand the region field under a shared startup lock."""

    # Importing the centralized model module registers every table on Base.metadata.
    from app import models as database_models  # noqa: F401

    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _SCHEMA_ADVISORY_LOCK_ID},
        )
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_expand_region_name_column)
