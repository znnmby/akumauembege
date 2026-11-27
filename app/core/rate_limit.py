from __future__ import annotations

import hashlib
import hmac
import logging
import math
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Sequence
from uuid import UUID, uuid4

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit_scripts import (
    _RECOMMENDATION_GATE_SCRIPT,
    _RELEASE_LOCK_SCRIPT,
    _ROLLING_LIMIT_SCRIPT,
)


logger = logging.getLogger(__name__)


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
            self._llm_capacity_key(),
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
                self._settings.llm_max_concurrent_requests,
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
        if result_code == 3:
            raise AppError(
                status_code=429,
                code="LLM_CAPACITY_EXCEEDED",
                message="Layanan pemilihan menu sedang penuh. Coba lagi sebentar.",
                retry_after=retry_after,
            )
        if result_code != 0:
            raise self._unavailable()
        return RecommendationGate(user_id=user_id, owner_token=owner)

    async def release_recommendation(self, gate: RecommendationGate) -> None:
        try:
            await self._redis.eval(
                _RELEASE_LOCK_SCRIPT,
                2,
                self._lock_key(f"recommendation:{gate.user_id}"),
                self._llm_capacity_key(),
                gate.owner_token,
            )
        except RedisError as exc:
            # Acquisition sets a TTL. A failed release must preserve the request outcome.
            logger.warning("Gagal melepas lock rekomendasi (%s)", type(exc).__name__)

    @staticmethod
    def _counter_key(key: str) -> str:
        return f"embege:rate:{key}"

    @staticmethod
    def _lock_key(key: str) -> str:
        return f"embege:lock:{key}"

    @staticmethod
    def _llm_capacity_key() -> str:
        return "embege:llm:active"

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
