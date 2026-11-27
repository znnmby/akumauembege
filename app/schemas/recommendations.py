from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.core.config import get_settings
from app.schemas.catalog import MenuSnapshot


_SCHEMA_LIMITS = get_settings()

RegionName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=2,
        max_length=_SCHEMA_LIMITS.max_region_name_length,
    ),
]
GeographicDescription = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=10,
        max_length=_SCHEMA_LIMITS.max_geographic_description_length,
    ),
]


class RecommendationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_name: RegionName
    geographic_description: GeographicDescription


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    region_name: str
    geographic_description: str
    selection_reason: str
    menu: MenuSnapshot
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(timezone.utc)
