"""Authenticated REST endpoint for synchronous menu recommendation creation."""

from fastapi import APIRouter, Request, status

from app.api.dependencies import (
    CurrentUser,
    get_rate_limiter,
    get_session_factory,
)
from app.modules.recommendations.schemas import (
    RecommendationCreateRequest,
    RecommendationResponse,
)
from app.modules.recommendations.service import RecommendationService

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
