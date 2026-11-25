from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass
from fastapi import Request
from ipaddress import ip_address
from redis.asyncio import Redis
from redis.exceptions import RedisError
from typing import Sequence
from uuid import UUID, uuid4
from app.core.config import Settings
from app.core.errors import AppError


_ROLLING_LIMIT_SCRIPT = """
local member = ARGV[1]
local current = redis.call('TIME')
local now_ms = (tonumber(current[1]) * 1000) + math.floor(tonumber(current[2]) / 1000)
local retry_ms = 0
local denied = false

for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[2 + ((index - 1) * 2)])
    local window_ms = tonumber(ARGV[3 + ((index - 1) * 2)])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        denied = true
        local blocking_rank = count - limit
        local blocking = redis.call(
            'ZRANGE', key, blocking_rank, blocking_rank, 'WITHSCORES'
        )
        if #blocking == 2 then
            local wait_ms = tonumber(blocking[2]) + window_ms - now_ms
            if wait_ms > retry_ms then
                retry_ms = wait_ms
            end
        end
    end
end

if denied then
    return {0, math.max(1, retry_ms)}
end

for index, key in ipairs(KEYS) do
    local window_ms = tonumber(ARGV[3 + ((index - 1) * 2)])
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
end

return {1, 0}
"""


_RECOMMENDATION_GATE_SCRIPT = """
local lock_key = KEYS[1]
local short_key = KEYS[2]
local long_key = KEYS[3]
local owner = ARGV[1]
local member = ARGV[2]
local lock_ttl_ms = tonumber(ARGV[3])
local short_limit = tonumber(ARGV[4])
local short_window_ms = tonumber(ARGV[5])
local long_limit = tonumber(ARGV[6])
local long_window_ms = tonumber(ARGV[7])

if redis.call('EXISTS', lock_key) == 1 then
    local lock_retry_ms = redis.call('PTTL', lock_key)
    if lock_retry_ms < 1 then
        lock_retry_ms = lock_ttl_ms
    end
    return {2, lock_retry_ms}
end

local current = redis.call('TIME')
local now_ms = (tonumber(current[1]) * 1000) + math.floor(tonumber(current[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', short_key, '-inf', now_ms - short_window_ms)
redis.call('ZREMRANGEBYSCORE', long_key, '-inf', now_ms - long_window_ms)

local retry_ms = 0
local short_count = redis.call('ZCARD', short_key)
if short_count >= short_limit then
    local blocking_rank = short_count - short_limit
    local blocking = redis.call(
        'ZRANGE', short_key, blocking_rank, blocking_rank, 'WITHSCORES'
    )
    if #blocking == 2 then
        retry_ms = math.max(retry_ms, tonumber(blocking[2]) + short_window_ms - now_ms)
    end
end
local long_count = redis.call('ZCARD', long_key)
if long_count >= long_limit then
    local blocking_rank = long_count - long_limit
    local blocking = redis.call(
        'ZRANGE', long_key, blocking_rank, blocking_rank, 'WITHSCORES'
    )
    if #blocking == 2 then
        retry_ms = math.max(retry_ms, tonumber(blocking[2]) + long_window_ms - now_ms)
    end
end

if retry_ms > 0 then
    return {1, math.max(1, retry_ms)}
end

redis.call('SET', lock_key, owner, 'PX', lock_ttl_ms)
redis.call('ZADD', short_key, now_ms, member)
redis.call('PEXPIRE', short_key, short_window_ms)
redis.call('ZADD', long_key, now_ms, member)
redis.call('PEXPIRE', long_key, long_window_ms)
return {0, 0}
"""


_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    key: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RecommendationGate:
    user_id: UUID
    owner_token: str


class RateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def enforce(self, rules: Sequence[RateLimitRule]) -> None:
        if not rules:
            return
        keys = [self._counter_key(rule.key) for rule in rules]
        arguments: list[str | int] = [uuid4().hex]
        for rule in rules:
            arguments.extend([rule.limit, rule.window_seconds * 1000])
        try:
            result = await self._redis.eval(
                _ROLLING_LIMIT_SCRIPT,
                len(keys),
                *keys,
                *arguments,
            )
        except RedisError as exc:
            raise self._unavailable() from exc
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise self._unavailable()
        try:
            allowed = int(result[0])
            retry_milliseconds = int(result[1])
        except (TypeError, ValueError) as exc:
            raise self._unavailable() from exc
        if allowed == 0:
            raise AppError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Batas permintaan terlampaui.",
                retry_after=self._seconds_from_milliseconds(retry_milliseconds),
            )
        if allowed != 1:
            raise self._unavailable()

    async def acquire_recommendation(self, user_id: UUID) -> RecommendationGate:
        owner = uuid4().hex
        request_id = uuid4().hex
        base = f"recommendation:{user_id}"
        keys = [
            self._lock_key(base),
            self._counter_key(f"{base}:60"),
            self._counter_key(
                f"{base}:{self._settings.recommendation_long_window_seconds}"
            ),
        ]
        try:
            result = await self._redis.eval(
                _RECOMMENDATION_GATE_SCRIPT,
                len(keys),
                *keys,
                owner,
                request_id,
                self._settings.recommendation_lock_ttl_seconds * 1000,
                self._settings.recommendation_minute_limit,
                60 * 1000,
                self._settings.recommendation_long_window_limit,
                self._settings.recommendation_long_window_seconds * 1000,
            )
        except RedisError as exc:
            raise self._unavailable() from exc
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise self._unavailable()
        try:
            result_code = int(result[0])
            retry_milliseconds = int(result[1])
        except (TypeError, ValueError) as exc:
            raise self._unavailable() from exc
        retry_after = self._seconds_from_milliseconds(retry_milliseconds)
        if result_code == 1:
            raise AppError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Batas permintaan rekomendasi terlampaui.",
                retry_after=retry_after,
            )
        if result_code == 2:
            raise AppError(
                status_code=429,
                code="RECOMMENDATION_IN_PROGRESS",
                message="Rekomendasi lain masih diproses.",
                retry_after=retry_after,
            )
        if result_code != 0:
            raise self._unavailable()
        return RecommendationGate(user_id=user_id, owner_token=owner)

    async def release_recommendation(self, gate: RecommendationGate) -> None:
        try:
            await self._redis.eval(
                _RELEASE_LOCK_SCRIPT,
                1,
                self._lock_key(f"recommendation:{gate.user_id}"),
                gate.owner_token,
            )
        except RedisError as exc:
            raise self._unavailable() from exc

    @staticmethod
    def _counter_key(key: str) -> str:
        return f"embege:rate:{key}"

    @staticmethod
    def _lock_key(key: str) -> str:
        return f"embege:lock:{key}"

    @staticmethod
    def _seconds_from_milliseconds(value: int) -> int:
        return max(1, math.ceil(max(1, value) / 1000))

    @staticmethod
    def _unavailable() -> AppError:
        return AppError(
            status_code=503,
            code="RATE_LIMIT_UNAVAILABLE",
            message="Layanan pembatasan permintaan tidak tersedia.",
        )


def email_rate_limit_digest(normalized_email: str, settings: Settings) -> str:
    return hmac.new(
        settings.jwt_secret_key.get_secret_value().encode("utf-8"),
        normalized_email.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def get_client_ip(request: Request, trusted_proxy_ips: Sequence[str]) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    try:
        normalized_peer = str(ip_address(peer))
    except ValueError:
        normalized_peer = peer
    trusted = set(trusted_proxy_ips)
    if normalized_peer not in trusted:
        return normalized_peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return normalized_peer
    chain: list[str] = []
    for raw_value in forwarded.split(","):
        try:
            chain.append(str(ip_address(raw_value.strip())))
        except ValueError:
            return normalized_peer
    for address in reversed([*chain, normalized_peer]):
        if address not in trusted:
            return address
    return chain[0] if chain else normalized_peer


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def close_redis_client(client: Redis) -> None:
    await client.aclose()
