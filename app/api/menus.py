from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.auth import ReadIdentity, get_settings_from_request
from app.core.errors import AppError
from app.core.limits import MAX_CURSOR_LENGTH, MAX_PAGE_SIZE, MAX_SEARCH_LENGTH
from app.core.pagination import (
    decode_menu_cursor,
    encode_menu_cursor,
    normalize_search,
    validate_limit,
)
from app.schemas import MenuSnapshot, PaginatedResponse
from app.services.catalog import CatalogService


router = APIRouter(prefix="/menus", tags=["menus"])


@router.get("", response_model=PaginatedResponse[MenuSnapshot])
async def list_menus(
    request: Request,
    identity: ReadIdentity,
    search: Annotated[str | None, Query(max_length=MAX_SEARCH_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    after: Annotated[str | None, Query(min_length=1, max_length=MAX_CURSOR_LENGTH)] = None,
) -> PaginatedResponse[MenuSnapshot]:
    settings = get_settings_from_request(request)
    validate_limit(limit, maximum=settings.read_max_page_size)
    search = normalize_search(search)
    after_id = decode_menu_cursor(after, search)
    service: CatalogService = request.app.state.catalog_service
    page = service.list_active_menus(search, first=limit, after_id=after_id)
    return PaginatedResponse(
        items=[service.make_snapshot(menu) for menu in page.items],
        next_cursor=(
            encode_menu_cursor(page.items[-1].id, search)
            if page.has_next_page else None
        ),
        has_next_page=page.has_next_page,
    )


@router.get("/{menu_id}", response_model=MenuSnapshot)
async def get_menu(
    menu_id: str,
    request: Request,
    identity: ReadIdentity,
) -> MenuSnapshot:
    service: CatalogService = request.app.state.catalog_service
    menu = service.get_active_menu(menu_id)
    if menu is None:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Menu tidak ditemukan.",
        )
    return service.make_snapshot(menu)
