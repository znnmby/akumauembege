from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.schemas.auth import UserResponse
from app.services.auth import AuthService


@pytest.mark.parametrize("path, method", [
    ("/auth/me", "get"),
    ("/auth/change-password", "post"),
    ("/auth/logout", "post"),
    ("/recommendations", "post"),
    ("/recommendations", "get"),
    ("/recommendations/{recommendation_id}", "get"),
])
def test_openapi_documents_bearer_for_protected_operations(api, path, method):
    schema = api.client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    bearer_names = {
        name for name, scheme in schemes.items()
        if scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
    }
    assert bearer_names
    operation = schema["paths"][path][method]
    requirements = operation.get("security", schema.get("security", []))
    assert requirements
    assert all(requirement for requirement in requirements)
    assert any(
        name in requirement and requirement[name] == []
        for requirement in requirements for name in bearer_names
    )


@pytest.mark.parametrize("path, method", [
    ("/health", "get"),
    ("/auth/register", "post"),
    ("/auth/login", "post"),
    ("/menus", "get"),
    ("/menus/{menu_id}", "get"),
])
def test_openapi_keeps_public_operations_without_required_bearer(api, path, method):
    schema = api.client.get("/openapi.json").json()
    operation = schema["paths"][path][method]
    assert not operation.get("security", schema.get("security"))


@pytest.mark.parametrize("kind", [
    "missing", "basic", "empty", "invalid", "expired", "revoked",
])
def test_me_retains_authentication_errors_and_failure_budget(api, monkeypatch, kind):
    get_me = AsyncMock()
    monkeypatch.setattr(AuthService, "get_me", get_me)
    authorization = {
        "missing": None,
        "basic": "Basic credentials",
        "empty": "Bearer",
        "invalid": "Bearer invalid-token",
    }.get(kind)
    if kind in {"expired", "revoked"}:
        token = api.token_for(api.owner, expired=kind == "expired", revoked=kind == "revoked")
        authorization = f"Bearer {token}"
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = api.client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    get_me.assert_not_awaited()
    api.limiter.enforce.assert_awaited_once()
    assert api.limiter.enforce.call_args.args[0][0].key.startswith("auth-failure:ip:")


@pytest.mark.parametrize("prefix", ["Bearer ", "bearer ", "Bearer\t"])
def test_me_retains_existing_header_parsing_and_accepts_valid_token(api, monkeypatch, prefix):
    now = datetime.now(timezone.utc)
    profile = UserResponse(
        id=api.owner, email="user@example.com", created_at=now, updated_at=now,
    )
    get_me = AsyncMock(return_value=profile)
    monkeypatch.setattr(AuthService, "get_me", get_me)
    response = api.client.get("/auth/me", headers={
        "Authorization": prefix + api.token_for(api.owner),
    })
    assert response.status_code == 200
    assert response.json() == profile.model_dump(mode="json")
    get_me.assert_awaited_once_with(api.owner)
    api.limiter.enforce.assert_awaited_once()
    assert api.limiter.enforce.call_args.args[0][0].key == f"me:user:{api.owner}"
