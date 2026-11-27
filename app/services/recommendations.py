from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.core.pagination import decode_history_cursor, encode_history_cursor
from app.core.rate_limit import RateLimiter
from app.db import AsyncSessionFactory
from app.models import Recommendation
from app.repositories.recommendations import RecommendationRepository
from app.schemas.catalog import MenuSnapshot
from app.schemas.llm import LLMCandidate, LLMSelectionRequest
from app.schemas.pagination import PaginatedResponse
from app.schemas.recommendations import (
    RecommendationCreateRequest,
    RecommendationResponse,
)
from app.services.catalog import CatalogService
from app.services.llm import (
    LLMClient,
    LLMInvalidResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)


logger = logging.getLogger(__name__)


class RecommendationService:
    def __init__(
        self,
        *,
        catalog_service: CatalogService,
        llm_client: LLMClient,
        rate_limiter: RateLimiter,
        session_factory: AsyncSessionFactory,
        repository: RecommendationRepository | None = None,
    ) -> None:
        self._catalog_service = catalog_service
        self._llm_client = llm_client
        self._rate_limiter = rate_limiter
        self._session_factory = session_factory
        self._repository = repository or RecommendationRepository()

    @staticmethod
    def _response(record: Recommendation) -> RecommendationResponse:
        return RecommendationResponse(
            id=record.id,
            region_name=record.region_name,
            geographic_description=record.geographic_description,
            selection_reason=record.selection_reason,
            menu=MenuSnapshot.model_validate(record.menu_snapshot),
            created_at=record.created_at,
        )

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        limit: int,
        after: str | None,
    ) -> PaginatedResponse[RecommendationResponse]:
        cursor = decode_history_cursor(after, user_id)
        try:
            async with self._session_factory() as session:
                records, has_next_page = await self._repository.list_for_user(
                    session, user_id=user_id, first=limit, after=cursor,
                )
        except SQLAlchemyError as exc:
            raise AppError(
                status_code=503,
                code="DATABASE_UNAVAILABLE",
                message="Layanan riwayat tidak tersedia.",
            ) from exc
        return PaginatedResponse(
            items=[self._response(record) for record in records],
            next_cursor=(
                encode_history_cursor(records[-1].created_at, records[-1].id, user_id)
                if has_next_page else None
            ),
            has_next_page=has_next_page,
        )

    async def get_for_user(
        self, *, user_id: UUID, recommendation_id: UUID,
    ) -> RecommendationResponse:
        try:
            async with self._session_factory() as session:
                record = await self._repository.get_for_user(
                    session, user_id=user_id, recommendation_id=recommendation_id,
                )
        except SQLAlchemyError as exc:
            raise AppError(
                status_code=503,
                code="DATABASE_UNAVAILABLE",
                message="Layanan riwayat tidak tersedia.",
            ) from exc
        if record is None:
            raise AppError(
                status_code=404,
                code="NOT_FOUND",
                message="Rekomendasi tidak ditemukan.",
            )
        return self._response(record)

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
