"""Async PostgreSQL access for recommendation history."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Recommendation


class RecommendationRepository:
    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        menu_id: str,
        region_name: str,
        geographic_description: str,
        selection_reason: str,
        menu_snapshot: dict[str, Any],
        llm_model: str,
    ) -> Recommendation:
        recommendation = Recommendation(
            user_id=user_id,
            menu_id=menu_id,
            region_name=region_name,
            geographic_description=geographic_description,
            selection_reason=selection_reason,
            menu_snapshot=menu_snapshot,
            llm_model=llm_model,
        )
        session.add(recommendation)
        await session.flush()
        return recommendation

    async def get_for_user(
        self,
        session: AsyncSession,
        *,
        recommendation_id: UUID,
        user_id: UUID,
    ) -> Recommendation | None:
        statement = select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.user_id == user_id,
        )
        return await session.scalar(statement)

    async def list_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        first: int,
        after: tuple[datetime, UUID] | None,
    ) -> tuple[list[Recommendation], bool]:
        statement = select(Recommendation).where(Recommendation.user_id == user_id)
        if after is not None:
            created_at, recommendation_id = after
            statement = statement.where(
                or_(
                    Recommendation.created_at < created_at,
                    and_(
                        Recommendation.created_at == created_at,
                        Recommendation.id < recommendation_id,
                    ),
                )
            )
        statement = statement.order_by(
            Recommendation.created_at.desc(),
            Recommendation.id.desc(),
        ).limit(first + 1)
        records = list((await session.scalars(statement)).all())
        return records[:first], len(records) > first
