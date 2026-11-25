"""Authenticated, query-only GraphQL recommendation history resolvers."""

from __future__ import annotations

from uuid import UUID

import strawberry
from graphql import GraphQLError
from sqlalchemy.exc import SQLAlchemyError
from strawberry.types import Info

from app.api.graphql.context import GraphQLContext
from app.api.graphql.pagination import (
    PageInfo,
    bad_user_input,
    decode_history_cursor,
    encode_history_cursor,
    validate_first,
)
from app.api.graphql.security import require_authenticated
from app.modules.recommendations.graphql.types import (
    RecommendationConnection,
    RecommendationEdge,
    RecommendationType,
)


@strawberry.type
class RecommendationQuery:
    @strawberry.field(name="myHistory")
    async def my_history(
        self,
        info: Info[GraphQLContext, None],
        first: int = 20,
        after: str | None = None,
    ) -> RecommendationConnection:
        context = info.context
        authenticated_user = require_authenticated(context)
        page_size = validate_first(first, maximum=context.settings.graphql_max_page_size)
        cursor = decode_history_cursor(after, authenticated_user.user_id)
        try:
            async with context.session_factory() as session:
                records, has_next_page = await context.recommendation_repository.list_for_user(
                    session,
                    user_id=authenticated_user.user_id,
                    first=page_size,
                    after=cursor,
                )
        except SQLAlchemyError:
            raise GraphQLError(
                "Layanan riwayat tidak tersedia.",
                extensions={"code": "SERVICE_UNAVAILABLE"},
            ) from None

        edges = [
            RecommendationEdge(
                cursor=encode_history_cursor(record.created_at, record.id, authenticated_user.user_id),
                node=RecommendationType.from_record(record),
            )
            for record in records
        ]
        return RecommendationConnection(
            edges=edges,
            page_info=PageInfo(
                end_cursor=edges[-1].cursor if edges else None,
                has_next_page=has_next_page,
            ),
        )

    @strawberry.field(name="myRecommendation")
    async def my_recommendation(
        self,
        info: Info[GraphQLContext, None],
        id: strawberry.ID,
    ) -> RecommendationType | None:
        context = info.context
        authenticated_user = require_authenticated(context)
        try:
            recommendation_id = UUID(str(id))
        except (TypeError, ValueError):
            bad_user_input("ID rekomendasi bukan UUID yang valid.")

        try:
            async with context.session_factory() as session:
                record = await context.recommendation_repository.get_for_user(
                    session,
                    recommendation_id=recommendation_id,
                    user_id=authenticated_user.user_id,
                )
        except SQLAlchemyError:
            raise GraphQLError(
                "Layanan riwayat tidak tersedia.",
                extensions={"code": "SERVICE_UNAVAILABLE"},
            ) from None
        return None if record is None else RecommendationType.from_record(record)
