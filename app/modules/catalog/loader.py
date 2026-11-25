"""Load and validate the catalog once during application startup."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import ValidationError

from app.modules.catalog.schemas import CatalogDocument, CatalogMenu

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "app" / "db" / "data" / "data.json"

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

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
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
