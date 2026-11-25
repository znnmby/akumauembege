from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import Settings
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


async def initialize_database(engine: AsyncEngine) -> None:
    """Create missing tables while serializing concurrent startup attempts."""

    # Importing the centralized model module registers every table on Base.metadata.
    from app import models as database_models  # noqa: F401

    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _SCHEMA_ADVISORY_LOCK_ID},
        )
        await connection.run_sync(Base.metadata.create_all)
