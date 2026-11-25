"""GraphQL-only public projections for recommendation history."""

from __future__ import annotations

from datetime import datetime, timezone

import strawberry

from app.api.graphql.pagination import PageInfo
from app.modules.catalog.schemas import MenuSnapshot
from app.db.models import Recommendation


@strawberry.type(name="MenuSnapshot")
class MenuSnapshotType:
    id: strawberry.ID
    name: str
    description: str
    ascii_art: list[str] = strawberry.field(name="asciiArt")

    @classmethod
    def from_snapshot(cls, snapshot: MenuSnapshot) -> "MenuSnapshotType":
        return cls(
            id=strawberry.ID(snapshot.id),
            name=snapshot.name,
            description=snapshot.description,
            ascii_art=list(snapshot.ascii_art),
        )


@strawberry.type(name="Recommendation")
class RecommendationType:
    id: strawberry.ID
    region_name: str = strawberry.field(name="regionName")
    geographic_description: str = strawberry.field(name="geographicDescription")
    selection_reason: str = strawberry.field(name="selectionReason")
    menu: MenuSnapshotType
    created_at: datetime = strawberry.field(name="createdAt")

    @classmethod
    def from_record(cls, record: Recommendation) -> "RecommendationType":
        snapshot = MenuSnapshot.model_validate(record.menu_snapshot)
        return cls(
            id=strawberry.ID(str(record.id)),
            region_name=record.region_name,
            geographic_description=record.geographic_description,
            selection_reason=record.selection_reason,
            menu=MenuSnapshotType.from_snapshot(snapshot),
            created_at=record.created_at.astimezone(timezone.utc),
        )


@strawberry.type(name="RecommendationEdge")
class RecommendationEdge:
    cursor: str
    node: RecommendationType


@strawberry.type(name="RecommendationConnection")
class RecommendationConnection:
    edges: list[RecommendationEdge]
    page_info: PageInfo = strawberry.field(name="pageInfo")
