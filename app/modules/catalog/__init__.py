"""Validated, read-only menu catalog."""

from app.modules.catalog.loader import CatalogLoader
from app.modules.catalog.service import CatalogService

__all__ = ["CatalogLoader", "CatalogService"]
