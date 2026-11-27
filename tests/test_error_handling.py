from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.rate_limit import RateLimiter
from app.repositories.auth import AuthRepository
from app.schemas.llm import LLMSelectionResponse
from app.services.auth import AuthService
from app.services.llm import LLMTimeoutError


@pytest.mark.parametrize("timeout", [False, True])
def test_release_failure_preserves_recommendation_result(api, caplog, timeout):
    redis = SimpleNamespace(eval=AsyncMock(side_effect=[
        [0, 0], RedisConnectionError("simulated cleanup failure")
    ]))
    api.app.state.rate_limiter = RateLimiter(redis, api.settings)
    api.app.state.llm_client = SimpleNamespace(
        model="test-model",
        select_menu=AsyncMock(
            return_value=LLMSelectionResponse(menu_id="menu-001", selection_reason="Bahan lokal"),
            side_effect=LLMTimeoutError() if timeout else None,
        ),
    )
    response = api.client.post("/recommendations", headers=api.headers, json={
        "region_name": "Pesisir", "geographic_description": "Daerah pesisir utara",
    })
    assert redis.eval.await_count == 2
    assert "Gagal melepas lock" in caplog.text
    api.app.state.rate_limiter = api.limiter
    records = api.client.get("/recommendations", headers=api.headers).json()["items"]
    if timeout:
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "LLM_TIMEOUT"
        assert len(records) == 3
    else:
        assert response.status_code == 201
        assert len(records) == 4
        assert response.json() in records


def test_registration_only_reports_duplicate_email_for_email_conflict(api, monkeypatch, caplog):
    body = {"email": "test@example.com", "password": "test-password-123"}
    assert api.client.post("/auth/register", json=body).status_code == 201
    duplicate = api.client.post("/auth/register", json={**body, "email": "TEST@example.com"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"

    error = IntegrityError("internal SQL", {}, ValueError("private diagnostic"))
    monkeypatch.setattr(AuthRepository, "create_user", AsyncMock(side_effect=error))
    failure = api.client.post("/auth/register", json=body)
    assert failure.status_code == 500
    assert failure.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "Registration constraint failure" in caplog.text
    assert "private diagnostic" not in caplog.text + failure.text


def test_openapi_uses_actual_error_model(api):
    responses = api.app.openapi()["paths"]["/auth/login"]["post"]["responses"]
    for code in ("401", "422", "429", "500", "503"):
        assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert "Retry-After" in responses["429"]["headers"]
    assert "WWW-Authenticate" in responses["401"]["headers"]
    result = api.client.post("/auth/login", json={})
    assert result.status_code == 422
    assert {e["field"] for e in result.json()["error"]["details"]} == {"email", "password"}


def test_failed_bearer_budget_shared_across_routes(api):
    redis = SimpleNamespace(eval=AsyncMock(side_effect=[[1, 0], [0, 2200]]))
    api.app.state.rate_limiter = RateLimiter(redis, api.settings)
    rejected = api.client.get("/menus", headers={"Authorization": "Bearer invalid"})
    limited = api.client.get("/auth/me")
    assert rejected.status_code == 401
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "3"
    assert limited.json()["error"]["retry_after"] == 3
    read_args, me_args = [call.args for call in redis.eval.await_args_list]
    assert read_args[3] == me_args[2]
    assert ":auth-failure:ip:" in me_args[2]
    assert me_args[-2:] == (api.settings.invalid_auth_limit, api.settings.invalid_auth_window_seconds * 1000)


@pytest.mark.parametrize("path", ["/auth/me", "/menus"])
def test_auth_database_error_does_not_consume_failure_budget(api, monkeypatch, path):
    monkeypatch.setattr(AuthRepository, "get_valid_identity", AsyncMock(side_effect=SQLAlchemyError()))
    response = api.client.get(path, headers=api.headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    api.limiter.enforce.assert_not_awaited()


def test_valid_logout_is_independent_of_redis(api, monkeypatch):
    logout = AsyncMock()
    monkeypatch.setattr(AuthService, "logout", logout)
    response = api.client.post("/auth/logout", headers=api.headers)
    assert response.status_code == 200
    assert response.json() == {"message": "Logout successful."}
    logout.assert_awaited_once()
    api.limiter.enforce.assert_not_awaited()
