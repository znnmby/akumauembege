import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import String, Text

from app import db
from app.services.recommendations import RecommendationService
from app.core.config import get_settings
from app.core.pagination import decode_menu_cursor, encode_menu_cursor
from app.core.rate_limit import RateLimiter
from app.schemas import CatalogMenu, LLMCandidate, LLMSelectionRequest, RecommendationResponse
from app.services.llm import LLMClient, LLMInvalidResponseError


@pytest.mark.parametrize("path", ["/menus", "/recommendations"])
def test_page_sizes_above_previous_limit_are_accepted(api, path):
    response = api.client.get(path, params={"limit": 1000}, headers=api.headers)
    assert response.status_code == 200


def test_search_and_unicode_cursor_support_expanded_limit(api):
    search = "魚" * 1500
    assert api.client.get("/menus", params={"search": search}).status_code == 200
    cursor = encode_menu_cursor("menu-001", search)
    assert len(cursor) > 2048
    assert decode_menu_cursor(cursor, search) == "menu-001"
    response = api.client.get("/menus", params={"search": search, "after": cursor})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_expanded_recommendation_inputs_reach_llm_schema(api, monkeypatch):
    async def accept(service, *, user_id, request):
        payload = LLMSelectionRequest(
            region_name=request.region_name,
            geographic_description=request.geographic_description,
            candidates=[LLMCandidate(id="menu-001", name="Menu", description="Katalog")],
        )
        assert len(payload.region_name) == 1500
        assert len(payload.geographic_description) == 20000
        return RecommendationResponse(
            id=user_id, region_name=payload.region_name,
            geographic_description=payload.geographic_description,
            selection_reason="Pilihan", menu=api.snapshot,
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(RecommendationService, "create", accept)
    body = {"region_name": "R" * 1500, "geographic_description": "G" * 20000}
    assert api.client.post("/recommendations", json=body, headers=api.headers).status_code == 201
    for field in body:
        too_long = {**body, field: body[field] + "x"}
        assert api.client.post("/recommendations", json=too_long, headers=api.headers).status_code == 422


def test_large_llm_response_uses_new_token_and_size_limits(api):
    reason = "Alasan " * 1428 + "xxxx"
    assert len(reason) == 10000
    create = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "menu_id": "menu-001", "selection_reason": reason,
        })))
    ]))
    provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = LLMClient(api.settings, client=provider)
    request = LLMSelectionRequest(
        region_name="Pesisir", geographic_description="Wilayah pesisir utara",
        candidates=[LLMCandidate(id="menu-001", name="Menu", description="Katalog")],
    )
    assert asyncio.run(llm.select_menu(request)).selection_reason == reason
    assert create.call_args.kwargs["max_tokens"] == 5120
    # Whitespace counts toward the byte ceiling without affecting valid JSON.
    raw = json.dumps({"menu_id": "menu-001", "selection_reason": "Sesuai"})
    assert llm._parse_response(raw + " " * (163840 - len(raw))).menu_id == "menu-001"
    with pytest.raises(LLMInvalidResponseError):
        llm._parse_response(raw + " " * (163841 - len(raw)))
    with pytest.raises(LLMInvalidResponseError):
        llm._parse_response(json.dumps({"menu_id": "menu-001", "selection_reason": reason + "x"}))


def test_catalog_expanded_content_reaches_provider_schema():
    menu = CatalogMenu(
        id="menu-001", name="M" * 1500, description="D" * 20000,
        is_active=True, your_food=["x" * 2000] * 1000,
    )
    assert LLMCandidate(id=menu.id, name=menu.name, description=menu.description).name == menu.name


def test_recommendation_quota_is_tenfold_with_same_windows(api):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[0, 0]))
    limiter = RateLimiter(redis, api.settings)
    asyncio.run(limiter.acquire_recommendation(api.owner))
    assert redis.eval.call_args.args[8:] == (1200000, 100, 60000, 1000, 10800000, 5)


def test_other_rate_limits_and_security_settings():
    settings = get_settings()
    for prefix, limit, window in [
        ("register", 50, 3600), ("login", 100, 900),
        ("change_password", 50, 900), ("me", 600, 60), ("read", 600, 60),
    ]:
        assert getattr(settings, f"{prefix}_limit") == limit
        assert getattr(settings, f"{prefix}_window_seconds") == window
    assert settings.access_token_expire_minutes == 30
    assert settings.recommendation_lock_ttl_seconds > settings.llm_timeout_seconds


@pytest.mark.parametrize("column_type, needs_expansion", [
    (String(150), True), (String(1500), False), (String(2000), False), (Text(), False),
])
def test_existing_region_column_only_expands(monkeypatch, column_type, needs_expansion):
    connection = Mock()
    inspector = Mock()
    inspector.get_columns.return_value = [{"name": "region_name", "type": column_type}]
    monkeypatch.setattr(db, "inspect", lambda target: inspector)
    db._expand_region_name_column(connection)
    if needs_expansion:
        assert str(connection.execute.call_args.args[0]) == (
            "ALTER TABLE recommendations ALTER COLUMN region_name TYPE VARCHAR(1500)"
        )
    else:
        connection.execute.assert_not_called()
