"""Non-destructive table initialization."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base


_SCHEMA_ADVISORY_LOCK_ID = 7_619_883_523_247_101


async def initialize_database(engine: AsyncEngine) -> None:
    """Create missing tables while serializing concurrent startup attempts."""

    # Importing the centralized model module registers every table on Base.metadata.
    from app.db import models as database_models  # noqa: F401

    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _SCHEMA_ADVISORY_LOCK_ID},
        )
        await connection.run_sync(Base.metadata.create_all)
