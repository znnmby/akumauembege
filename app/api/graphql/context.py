"""Per-request GraphQL context with isolated database-session capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import authenticate_authorization
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.rate_limit import RateLimitRule, get_client_ip
from app.modules.auth.schemas import AuthenticatedUser
from app.modules.catalog.service import CatalogService
from app.modules.recommendations.repository import RecommendationRepository

if TYPE_CHECKING:
    from app.core.rate_limit import RateLimiter


@dataclass(frozen=True, slots=True)
class GraphQLContext:
    request: Request
    settings: Settings
    authenticated_user: AuthenticatedUser | None
    catalog_service: CatalogService
    session_factory: async_sessionmaker[AsyncSession]
    recommendation_repository: RecommendationRepository


async def build_graphql_context(request: Request) -> GraphQLContext:
    """Authenticate an optional bearer token and account for one GraphQL request."""

    settings: Settings = request.app.state.settings
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    rate_limiter: RateLimiter = request.app.state.rate_limiter
    peer_ip = get_client_ip(request, settings.trusted_proxy_ips)
    try:
        authenticated_user = await authenticate_authorization(
            request.headers.get("Authorization"),
            session_factory,
            settings,
            required=False,
        )
    except AppError:
        # Invalid/revoked bearer traffic is still request-level traffic. Count
        # it by peer identity before returning UNAUTHENTICATED.
        await rate_limiter.enforce(
            [
                RateLimitRule(
                    key=f"graphql:ip:{peer_ip}",
                    limit=settings.graphql_limit,
                    window_seconds=settings.graphql_window_seconds,
                )
            ]
        )
        raise

    if authenticated_user is not None:
        identity = f"user:{authenticated_user.user_id}"
    else:
        identity = f"ip:{peer_ip}"
    await rate_limiter.enforce(
        [
            RateLimitRule(
                key=f"graphql:{identity}",
                limit=settings.graphql_limit,
                window_seconds=settings.graphql_window_seconds,
            )
        ]
    )

    return GraphQLContext(
        request=request,
        settings=settings,
        authenticated_user=authenticated_user,
        catalog_service=request.app.state.catalog_service,
        session_factory=session_factory,
        recommendation_repository=RecommendationRepository(),
    )
