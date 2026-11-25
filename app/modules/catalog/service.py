"""Business operations over the immutable in-memory catalog."""

from dataclasses import dataclass

from app.modules.catalog.loader import CatalogLoader
from app.modules.catalog.schemas import CatalogMenu, MenuSnapshot


@dataclass(frozen=True, slots=True)
class CatalogPage:
    items: list[CatalogMenu]
    has_next_page: bool


class CatalogService:
    def __init__(self, loader: CatalogLoader) -> None:
        if not loader.loaded:
            raise ValueError("CatalogLoader harus dimuat sebelum CatalogService dibuat")
        self._loader = loader

    @staticmethod
    def normalize_search(search: str | None) -> str | None:
        if search is None:
            return None
        if not isinstance(search, str):
            raise ValueError("search harus berupa string")
        normalized = search.strip()
        if len(normalized) > 150:
            raise ValueError("search maksimal 150 karakter")
        return normalized or None

    def list_active_menus(
        self,
        search: str | None = None,
        *,
        first: int = 20,
        after_id: str | None = None,
    ) -> CatalogPage:
        if isinstance(first, bool) or not isinstance(first, int) or not 1 <= first <= 100:
            raise ValueError("first harus berada di antara 1 dan 100")

        normalized_search = self.normalize_search(search)
        needle = normalized_search.casefold() if normalized_search is not None else None
        menus = sorted(
            (menu for menu in self._loader.all_menus() if menu.is_active),
            key=lambda menu: menu.id,
        )
        if needle is not None:
            menus = [
                menu
                for menu in menus
                if needle in menu.name.casefold() or needle in menu.description.casefold()
            ]
        if after_id is not None:
            menus = [menu for menu in menus if menu.id > after_id]

        selected = menus[: first + 1]
        has_next_page = len(selected) > first
        return CatalogPage(
            items=[menu.model_copy(deep=True) for menu in selected[:first]],
            has_next_page=has_next_page,
        )

    def get_active_menu(self, menu_id: str) -> CatalogMenu | None:
        menu = self._loader.get_menu(menu_id)
        if menu is None or not menu.is_active:
            return None
        return menu.model_copy(deep=True)

    def get_active_candidates(self) -> list[CatalogMenu]:
        return sorted(
            [menu for menu in self._loader.all_menus() if menu.is_active],
            key=lambda menu: menu.id,
        )

    @staticmethod
    def make_snapshot(menu: CatalogMenu) -> MenuSnapshot:
        return MenuSnapshot(
            id=menu.id,
            name=menu.name,
            description=menu.description,
            ascii_art=list(menu.ascii_art),
        )
