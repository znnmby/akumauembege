from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from pydantic import ValidationError
from types import MappingProxyType
from typing import Any, Mapping
from app.schemas import CatalogDocument, CatalogMenu, MenuSnapshot


PROJECT_ROOT = Path(__file__).resolve().parents[2]

class CatalogLoadError(RuntimeError):
    """The configured catalog cannot be loaded safely."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"kunci JSON duplikat: {key}")
        result[key] = value
    return result


class CatalogLoader:
    """Own the canonical catalog and expose only defensive copies."""

    def __init__(self, path: str | Path) -> None:
        configured_path = Path(path)
        self.path = configured_path if configured_path.is_absolute() else PROJECT_ROOT / configured_path
        self._document: CatalogDocument | None = None
        self._menus_by_id: Mapping[str, CatalogMenu] = MappingProxyType({})

    @property
    def loaded(self) -> bool:
        return self._document is not None

    def load(self) -> CatalogDocument:
        """Read the configured UTF-8 file once and validate the whole document."""

        if self._document is not None:
            return self._document.model_copy(deep=True)

        try:
            raw_text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CatalogLoadError(f"Katalog tidak dapat dibaca dari {self.path}") from exc

        try:
            payload = json.loads(raw_text, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, ValueError) as exc:
            raise CatalogLoadError(f"Katalog di {self.path} bukan JSON yang valid: {exc}") from exc

        try:
            document = CatalogDocument.model_validate(payload)
        except ValidationError as exc:
            raise CatalogLoadError(f"Katalog di {self.path} tidak sesuai schema: {exc}") from exc

        self._document = document.model_copy(deep=True)
        self._menus_by_id = MappingProxyType(
            {menu.id: menu.model_copy(deep=True) for menu in document.menus}
        )
        return document.model_copy(deep=True)

    def _require_loaded(self) -> CatalogDocument:
        if self._document is None:
            raise CatalogLoadError("Katalog belum dimuat saat startup")
        return self._document

    def all_menus(self) -> list[CatalogMenu]:
        document = self._require_loaded()
        return [menu.model_copy(deep=True) for menu in document.menus]

    def get_menu(self, menu_id: str) -> CatalogMenu | None:
        self._require_loaded()
        menu = self._menus_by_id.get(menu_id)
        return None if menu is None else menu.model_copy(deep=True)


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
