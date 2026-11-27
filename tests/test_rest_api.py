from unittest.mock import AsyncMock
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.recommendations import RecommendationRepository
from app.services.auth import AuthService
from app.services.recommendations import RecommendationService
from app.core.errors import AppError
from app.schemas import RecommendationResponse


def test_change_password_returns_confirmation(api, monkeypatch):
    change_password = AsyncMock()
    monkeypatch.setattr(AuthService, "change_password", change_password)
    response = api.client.post("/auth/change-password", headers=api.headers, json={
        "current_password": "current-password", "new_password": "updated-password",
    })
    change_password.assert_awaited_once()
    assert response.status_code == 200
    assert response.json() == {"message": "Password changed successfully."}


def test_public_catalog_search_and_pagination(api):
    first = api.client.get("/menus", params={"search": " IKAN ", "limit": 1})
    assert first.status_code == 200
    page = first.json()
    assert [item["id"] for item in page["items"]] == ["menu-001"]
    assert "is_active" not in page["items"][0]
    assert page["has_next_page"] is True
    second = api.client.get("/menus", params={
        "search": "IKAN", "limit": 1, "after": page["next_cursor"],
    })
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["menu-003"]
    assert second.json()["next_cursor"] is None
    assert second.json()["has_next_page"] is False
    mismatch = api.client.get("/menus", params={
        "search": "ayam", "after": page["next_cursor"],
    })
    assert mismatch.status_code == 422


@pytest.mark.parametrize(("limit", "search"), [(1, "IKAN"), (2, ""), (2, " MENU- ")])
def test_menu_previous_page_round_trip(api, limit, search):
    params = {"limit": limit, "search": search}
    first = api.client.get("/menus", params=params).json()
    assert first["previous_cursor"] is None
    assert not first["has_previous_page"]
    second = api.client.get("/menus", params={**params, "after": first["next_cursor"]}).json()
    assert second["has_previous_page"]
    assert second["next_cursor"] is None
    previous = {**params, "before": second["previous_cursor"]}
    assert api.client.get("/menus", params=previous).json() == first
    assert api.client.get("/menus", params={**previous, "search": "lain"}).status_code == 422
    assert api.client.get("/menus", params={**previous, "after": first["next_cursor"]}).status_code == 422


def test_catalog_detail_and_empty_search(api):
    matched_by_id = api.client.get("/menus", params={"search": "001"})
    assert matched_by_id.status_code == 200
    assert [menu["id"] for menu in matched_by_id.json()["items"]] == ["menu-001"]
    response = api.client.get("/menus/menu-001")
    assert response.status_code == 200
    assert response.json()["your_food"] == ["+---+", "| o |", "+---+"]
    for menu_id in ("menu-004", "missing"):
        assert api.client.get(f"/menus/{menu_id}").status_code == 404
    assert api.client.get("/menus", params={"search": "unknown"}).json() == {
        "items": [], "next_cursor": None, "has_next_page": False,
        "previous_cursor": None, "has_previous_page": False,
    }


@pytest.mark.parametrize("path", ["/menus", "/recommendations"])
@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 1001}, {"limit": "abc"},
    {"after": ""}, {"after": "!invalid"}, {"after": "a" * 20481},
    {"after": "e30"},  # Valid base64 JSON, invalid cursor structure.
])
def test_invalid_pagination(api, path, params):
    response = api.client.get(path, params=params, headers=api.headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_configured_page_size_and_search_limit(api):
    api.settings.read_max_page_size = 20
    for path in ("/menus", "/recommendations"):
        assert api.client.get(path, params={"limit": 21}, headers=api.headers).status_code == 422
    assert api.client.get("/menus", params={"search": "x" * 1501}).status_code == 422


def test_history_is_private_paginated_and_uses_snapshots(api):
    cursor = None
    seen = []
    for expected_id in (3, 2, 1):
        params = {"limit": 1}
        if cursor:
            params["after"] = cursor
        response = api.client.get("/recommendations", params=params, headers=api.headers)
        assert response.status_code == 200
        page = response.json()
        assert page["items"][0]["id"] == str(UUID(int=expected_id))
        assert page["items"][0]["menu"] == api.snapshot
        assert "user_id" not in page["items"][0]
        seen.append(page["items"][0]["id"])
        cursor = page["next_cursor"]
        assert page["has_next_page"] is (expected_id != 1)
    assert cursor is None
    assert len(set(seen)) == 3
    response = api.client.get(f"/recommendations/{UUID(int=3)}", headers=api.headers)
    assert response.status_code == 200
    assert response.json()["menu"] == api.snapshot
    for number in (4, 999):
        response = api.client.get(f"/recommendations/{UUID(int=number)}", headers=api.headers)
        assert response.status_code == 404
    assert api.client.get("/recommendations/not-a-uuid", headers=api.headers).status_code == 422


def test_history_cursor_cannot_be_reused_by_other_user_or_resource(api):
    page = api.client.get("/recommendations", params={"limit": 1}, headers=api.headers).json()
    other_headers = {"Authorization": f"Bearer {api.token_for(api.other)}"}
    assert api.client.get("/recommendations", params={
        "after": page["next_cursor"],
    }, headers=other_headers).status_code == 422
    assert api.client.get("/menus", params={"after": page["next_cursor"]}).status_code == 422
    menu_cursor = api.client.get("/menus", params={"limit": 1}).json()["next_cursor"]
    assert api.client.get("/recommendations", params={
        "after": menu_cursor,
    }, headers=api.headers).status_code == 422


@pytest.mark.parametrize("path", ["/recommendations", f"/recommendations/{UUID(int=1)}"])
def test_history_requires_authentication(api, path):
    response = api.client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/menus", "/menus/menu-001", "/recommendations"])
@pytest.mark.parametrize("kind", ["malformed", "expired", "revoked"])
def test_invalid_credentials_are_rejected_and_rate_limited(api, path, kind):
    token = "invalid" if kind == "malformed" else api.token_for(
        api.owner, expired=kind == "expired", revoked=kind == "revoked",
    )
    response = api.client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    rule = api.limiter.enforce.call_args.args[0][0]
    assert rule.key.startswith("read:ip:")


def test_read_limits_share_user_key_and_preserve_retry_after(api):
    for path in ("/menus", "/recommendations"):
        assert api.client.get(path, headers=api.headers).status_code == 200
        rule = api.limiter.enforce.call_args.args[0][0]
        assert rule.key == f"read:user:{api.owner}"
        assert rule.limit == api.settings.read_limit
    api.limiter.enforce.side_effect = AppError(
        status_code=429, code="RATE_LIMIT_EXCEEDED", message="Batas permintaan.", retry_after=7,
    )
    response = api.client.get("/menus")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["error"]["retry_after"] == 7


@pytest.mark.parametrize("path, method", [
    ("/recommendations", "list_for_user"),
    (f"/recommendations/{UUID(int=1)}", "get_for_user"),
])
def test_database_failures_use_rest_error_response(api, monkeypatch, path, method):
    async def unavailable(*args, **kwargs):
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(RecommendationRepository, method, unavailable)
    response = api.client.get(path, headers=api.headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "private database details" not in response.text


def test_existing_recommendation_creation_route(api, monkeypatch):
    async def create(service, *, user_id, request):
        assert user_id == api.owner
        assert request.region_name == "Pesisir"
        return RecommendationResponse(
            id=UUID(int=9), region_name=request.region_name,
            geographic_description=request.geographic_description,
            selection_reason="Bahan lokal", menu=api.snapshot,
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(RecommendationService, "create", create)
    response = api.client.post("/recommendations", headers=api.headers, json={
        "region_name": "Pesisir", "geographic_description": "Wilayah pesisir utara",
    })
    assert response.status_code == 201


def test_openapi_documents_rest_endpoints(api):
    paths = api.client.get("/openapi.json").json()["paths"]
    assert set(paths) == {
        "/health", "/menus", "/menus/{menu_id}", "/recommendations",
        "/recommendations/{recommendation_id}", "/auth/register", "/auth/login",
        "/auth/me", "/auth/change-password", "/auth/logout",
    }
    assert {"get", "post"} <= paths["/recommendations"].keys()
    assert "/auth/login" in paths
