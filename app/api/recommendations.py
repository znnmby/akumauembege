from __future__ import annotations

import logging
from datetime import datetime
from fastapi import APIRouter, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typing import Any
from uuid import UUID
from app.api.auth import CurrentUser, get_rate_limiter, get_session_factory
from app.services.catalog import CatalogService
from app.core.errors import AppError
from app.services.llm import LLMClient, LLMInvalidResponseError, LLMTimeoutError, LLMUnavailableError
from app.models import Recommendation
from app.core.rate_limit import RateLimiter
from app.schemas import (
    LLMCandidate,
    LLMSelectionRequest,
    RecommendationCreateRequest,
    RecommendationResponse,
)


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


logger = logging.getLogger(__name__)


class RecommendationService:
    def __init__(
        self,
        *,
        catalog_service: CatalogService,
        llm_client: LLMClient,
        rate_limiter: RateLimiter,
        session_factory: async_sessionmaker[AsyncSession],
        repository: RecommendationRepository | None = None,
    ) -> None:
        self._catalog_service = catalog_service
        self._llm_client = llm_client
        self._rate_limiter = rate_limiter
        self._session_factory = session_factory
        self._repository = repository or RecommendationRepository()

    async def create(
        self,
        *,
        user_id: UUID,
        request: RecommendationCreateRequest,
    ) -> RecommendationResponse:
        candidates = self._catalog_service.get_active_candidates()
        if not candidates:
            raise AppError(
                status_code=503,
                code="CATALOG_UNAVAILABLE",
                message="Katalog menu aktif tidak tersedia.",
            )

        gate = await self._rate_limiter.acquire_recommendation(user_id)
        try:
            selection_request = LLMSelectionRequest(
                region_name=request.region_name,
                geographic_description=request.geographic_description,
                candidates=[
                    LLMCandidate(
                        id=candidate.id,
                        name=candidate.name,
                        description=candidate.description,
                    )
                    for candidate in candidates
                ],
            )
            try:
                selection = await self._llm_client.select_menu(selection_request)
            except LLMTimeoutError as exc:
                raise AppError(
                    status_code=504,
                    code="LLM_TIMEOUT",
                    message="Pemilihan menu melewati batas waktu.",
                ) from exc
            except LLMInvalidResponseError as exc:
                raise AppError(
                    status_code=502,
                    code="LLM_INVALID_RESPONSE",
                    message="Respons pemilihan menu tidak valid.",
                ) from exc
            except LLMUnavailableError as exc:
                raise AppError(
                    status_code=503,
                    code="LLM_UNAVAILABLE",
                    message="Layanan pemilihan menu tidak tersedia.",
                ) from exc

            if selection.menu_id is None:
                raise AppError(
                    status_code=422,
                    code="NO_SUITABLE_MENU",
                    message="Tidak ada menu yang sesuai dengan konteks daerah.",
                )

            candidate_by_id = {candidate.id: candidate for candidate in candidates}
            selected_menu = candidate_by_id.get(selection.menu_id)
            if selected_menu is None:
                raise AppError(
                    status_code=502,
                    code="LLM_INVALID_RESPONSE",
                    message="Respons pemilihan menu tidak valid.",
                )
            snapshot = self._catalog_service.make_snapshot(selected_menu)

            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        record = await self._repository.create(
                            session,
                            user_id=user_id,
                            menu_id=snapshot.id,
                            region_name=request.region_name,
                            geographic_description=request.geographic_description,
                            selection_reason=selection.selection_reason,
                            menu_snapshot=snapshot.model_dump(mode="json"),
                            llm_model=self._llm_client.model,
                        )
                return RecommendationResponse(
                    id=record.id,
                    region_name=record.region_name,
                    geographic_description=record.geographic_description,
                    selection_reason=record.selection_reason,
                    menu=snapshot,
                    created_at=record.created_at,
                )
            except SQLAlchemyError as exc:
                logger.warning(
                    "Gagal menyimpan recommendation (%s)", type(exc).__name__
                )
                raise AppError(
                    status_code=503,
                    code="DATABASE_UNAVAILABLE",
                    message="Penyimpanan rekomendasi tidak tersedia.",
                ) from exc
        finally:
            await self._rate_limiter.release_recommendation(gate)


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
