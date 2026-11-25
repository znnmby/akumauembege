"""Query-only GraphQL resolvers for the in-memory catalog."""

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from app.api.graphql.pagination import PageInfo, decode_menu_cursor, encode_menu_cursor
from app.modules.catalog.graphql.types import Menu, MenuConnection, MenuEdge
from app.modules.catalog.service import CatalogService


def _bad_user_input(message: str) -> GraphQLError:
    return GraphQLError(message, extensions={"code": "BAD_USER_INPUT"})


def _catalog_service(info: Info) -> CatalogService:
    service = getattr(info.context, "catalog_service", None)
    if not isinstance(service, CatalogService):
        raise GraphQLError(
            "Layanan katalog tidak tersedia.",
            extensions={"code": "SERVICE_UNAVAILABLE"},
        )
    return service


@strawberry.type
class CatalogQuery:
    @strawberry.field
    def menus(
        self,
        info: Info,
        search: str | None = None,
        first: int = 20,
        after: str | None = None,
    ) -> MenuConnection:
        settings = getattr(info.context, "settings", None)
        max_page_size = min(getattr(settings, "graphql_max_page_size", 100), 100)
        if isinstance(first, bool) or not 1 <= first <= max_page_size:
            raise _bad_user_input(f"first harus berada di antara 1 dan {max_page_size}.")

        service = _catalog_service(info)
        try:
            normalized_search = service.normalize_search(search)
        except ValueError as exc:
            raise _bad_user_input(str(exc)) from None

        last_id = decode_menu_cursor(after, normalized_search)
        try:
            page = service.list_active_menus(
                normalized_search,
                first=first,
                after_id=last_id,
            )
        except ValueError as exc:
            raise _bad_user_input(str(exc)) from None

        edges = [
            MenuEdge(
                cursor=encode_menu_cursor(menu.id, normalized_search),
                node=Menu.from_model(menu),
            )
            for menu in page.items
        ]
        return MenuConnection(
            edges=edges,
            page_info=PageInfo(
                end_cursor=edges[-1].cursor if edges else None,
                has_next_page=page.has_next_page,
            ),
        )

    @strawberry.field
    def menu(self, info: Info, id: strawberry.ID) -> Menu | None:
        catalog_menu = _catalog_service(info).get_active_menu(str(id))
        return None if catalog_menu is None else Menu.from_model(catalog_menu)
