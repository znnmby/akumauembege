"""Public GraphQL types for catalog menus."""

import strawberry

from app.api.graphql.pagination import PageInfo
from app.modules.catalog.schemas import CatalogMenu as CatalogMenuModel


@strawberry.type(name="Menu")
class Menu:
    id: strawberry.ID
    name: str
    description: str
    ascii_art: list[str] = strawberry.field(name="asciiArt")

    @classmethod
    def from_model(cls, menu: CatalogMenuModel) -> "Menu":
        return cls(
            id=strawberry.ID(menu.id),
            name=menu.name,
            description=menu.description,
            ascii_art=list(menu.ascii_art),
        )


@strawberry.type(name="MenuEdge")
class MenuEdge:
    cursor: str
    node: Menu


@strawberry.type(name="MenuConnection")
class MenuConnection:
    edges: list[MenuEdge]
    page_info: PageInfo = strawberry.field(name="pageInfo")
