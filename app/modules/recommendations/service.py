"""Recommendation orchestration across catalog, limiter, LLM, and PostgreSQL."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError
from app.core.rate_limit import RateLimiter
from app.integrations.llm.client import (
    LLMClient,
    LLMInvalidResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.integrations.llm.schemas import LLMCandidate, LLMSelectionRequest
from app.modules.catalog.service import CatalogService
from app.modules.recommendations.repository import RecommendationRepository
from app.modules.recommendations.schemas import (
    RecommendationCreateRequest,
    RecommendationResponse,
)

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
