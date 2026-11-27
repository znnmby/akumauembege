from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.api.dependencies import (
    AuthenticatedReadUser,
    CurrentUser,
    get_rate_limiter,
    get_session_factory,
    get_settings_from_request,
)
from app.core.errors import error_responses
from app.core.limits import MAX_CURSOR_LENGTH, MAX_PAGE_SIZE
from app.core.pagination import validate_limit
from app.schemas.pagination import PaginatedResponse
from app.schemas.recommendations import (
    RecommendationCreateRequest,
    RecommendationResponse,
)
from app.services.recommendations import RecommendationService


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _service(request: Request) -> RecommendationService:
    return RecommendationService(
        catalog_service=request.app.state.catalog_service,
        llm_client=request.app.state.llm_client,
        rate_limiter=get_rate_limiter(request),
        session_factory=get_session_factory(request),
    )

@router.post(
    "",
    response_model=RecommendationResponse,
    responses=error_responses(401, 422, 429, 500, 502, 503, 504),
    status_code=status.HTTP_201_CREATED,
)
async def create_recommendation(
    request: Request,
    body: RecommendationCreateRequest,
    current_user: CurrentUser,
) -> RecommendationResponse:
    return await _service(request).create(
        user_id=current_user.user_id,
        request=body,
    )


@router.get(
    "",
    response_model=PaginatedResponse[RecommendationResponse],
    responses=error_responses(401, 422, 429, 500, 503),
)
async def list_recommendations(
    request: Request,
    current_user: AuthenticatedReadUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    after: Annotated[str | None, Query(min_length=1, max_length=MAX_CURSOR_LENGTH)] = None,
) -> PaginatedResponse[RecommendationResponse]:
    validate_limit(limit, maximum=get_settings_from_request(request).read_max_page_size)
    return await _service(request).list_for_user(
        user_id=current_user.user_id, limit=limit, after=after,
    )


@router.get(
    "/{recommendation_id}",
    response_model=RecommendationResponse,
    responses=error_responses(401, 404, 422, 429, 500, 503),
)
async def get_recommendation(
    recommendation_id: UUID,
    request: Request,
    current_user: AuthenticatedReadUser,
) -> RecommendationResponse:
    return await _service(request).get_for_user(
        user_id=current_user.user_id, recommendation_id=recommendation_id,
    )
