import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter, RecommendationGate


def test_capacity_can_be_configured_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONCURRENT_REQUESTS", "7")
    assert Settings().llm_max_concurrent_requests == 7


@pytest.mark.parametrize("value", [0, -1])
def test_capacity_rejects_non_positive_values(value):
    values = get_settings().model_dump()
    values["llm_max_concurrent_requests"] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_full_capacity_rejects_before_provider_call(api):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[3, 1000]))
    api.app.state.rate_limiter = RateLimiter(redis, api.settings)
    provider = AsyncMock()
    api.app.state.llm_client = SimpleNamespace(select_menu=provider)
    response = api.client.post("/recommendations", headers=api.headers, json={
        "region_name": "Pesisir", "geographic_description": "Wilayah pesisir utara",
    })
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "LLM_CAPACITY_EXCEEDED"
    assert response.headers["Retry-After"] == "1"
    provider.assert_not_awaited()
    assert redis.eval.await_count == 1


@asynccontextmanager
async def isolated_limiter(settings):
    """Run real Lua against opt-in Redis, with unique keys and final cleanup."""
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run Redis concurrency integration tests")
    namespace = f"embege-test:{uuid4().hex}:"
    client = Redis.from_url(url, decode_responses=True)

    class NamespacedRedis:
        async def eval(self, script, number, *args):
            keys = [namespace + key for key in args[:number]]
            return await client.eval(script, number, *keys, *args[number:])

    limiter = RateLimiter(NamespacedRedis(), settings)
    try:
        yield limiter, client, namespace
    finally:
        keys = [key async for key in client.scan_iter(match=namespace + "*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


def test_shared_capacity_is_atomic_and_busy_requests_preserve_quota():
    async def scenario():
        settings = get_settings().model_copy(update={"llm_max_concurrent_requests": 2})
        async with isolated_limiter(settings) as (first, client, namespace):
            # Separate instances model API workers sharing the same Redis.
            second = RateLimiter(first._redis, settings)
            users = [uuid4() for _ in range(12)]
            results = await asyncio.gather(*[
                (first if index % 2 else second).acquire_recommendation(user)
                for index, user in enumerate(users)
            ], return_exceptions=True)
            gates = [result for result in results if isinstance(result, RecommendationGate)]
            rejected = [user for user, result in zip(users, results) if isinstance(result, AppError)]
            assert len(gates) == 2
            assert len(rejected) == 10
            for user, result in zip(users, results):
                if isinstance(result, AppError):
                    assert result.code == "LLM_CAPACITY_EXCEEDED"
                    assert not await client.exists(namespace + f"embege:rate:recommendation:{user}:60")
                    assert not await client.exists(namespace + f"embege:lock:recommendation:{user}")
            with pytest.raises(AppError, match="RECOMMENDATION_IN_PROGRESS"):
                await second.acquire_recommendation(gates[0].user_id)
            await second.release_recommendation(gates[0])
            replacement = await first.acquire_recommendation(gates[0].user_id)
            # A delayed duplicate cleanup must not remove the replacement's lease.
            await second.release_recommendation(gates[0])
            assert await client.zcard(namespace + "embege:llm:active") == 2
            with pytest.raises(AppError, match="LLM_CAPACITY_EXCEEDED"):
                await first.acquire_recommendation(rejected[0])
            await first.release_recommendation(replacement)
            admitted = await second.acquire_recommendation(rejected[0])
            await second.release_recommendation(admitted)
            await first.release_recommendation(gates[1])
            assert await client.zcard(namespace + "embege:llm:active") == 0

    asyncio.run(scenario())


def test_abandoned_capacity_expires_and_late_cleanup_preserves_new_request():
    async def scenario():
        settings = get_settings().model_copy(update={
            "llm_max_concurrent_requests": 1,
            "llm_timeout_seconds": 1,
            "recommendation_lock_ttl_seconds": 2,
        })
        async with isolated_limiter(settings) as (limiter, client, namespace):
            old = await limiter.acquire_recommendation(uuid4())
            assert await client.pttl(namespace + "embege:llm:active") > 0
            with pytest.raises(AppError, match="LLM_CAPACITY_EXCEEDED"):
                await limiter.acquire_recommendation(uuid4())
            await asyncio.sleep(2.05)
            new = await limiter.acquire_recommendation(old.user_id)
            await limiter.release_recommendation(old)
            with pytest.raises(AppError, match="RECOMMENDATION_IN_PROGRESS"):
                await limiter.acquire_recommendation(new.user_id)
            with pytest.raises(AppError, match="LLM_CAPACITY_EXCEEDED"):
                await limiter.acquire_recommendation(uuid4())
            await limiter.release_recommendation(new)

    asyncio.run(scenario())
