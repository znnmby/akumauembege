from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, unauthorized
from app.core.rate_limit import RateLimitRule, RateLimiter, get_client_ip
from app.db import AsyncSessionFactory
from app.schemas.auth import AuthenticatedUser
from app.services.authentication import authenticate_authorization


_bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    auto_error=False,
)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
]


def get_settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request) -> AsyncSessionFactory:
    return request.app.state.session_factory


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(request)
    async with factory() as session:
        yield session


def invalid_auth_rule(request: Request) -> RateLimitRule:
    settings = get_settings_from_request(request)
    peer_ip = get_client_ip(request, settings.trusted_proxy_ips)
    return RateLimitRule(
        key=f"auth-failure:ip:{peer_ip}",
        limit=settings.invalid_auth_limit,
        window_seconds=settings.invalid_auth_window_seconds,
    )


async def get_current_user(
    request: Request,
    _credentials: BearerCredentials = None,
) -> AuthenticatedUser:
    # HTTPBearer supplies OpenAPI metadata; raw-header validation stays below.
    try:
        identity = await authenticate_authorization(
            request.headers.get("authorization"),
            get_session_factory(request),
            get_settings_from_request(request),
            required=True,
        )
    except AppError as exc:
        if exc.status_code == 401:
            await get_rate_limiter(request).enforce([invalid_auth_rule(request)])
        raise
    if identity is None:  # Required mode guarantees this; keeps the type explicit.
        raise unauthorized()
    return identity


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def get_read_identity(request: Request) -> AuthenticatedUser | None:
    """Authenticate read requests and share their limit across both resources."""
    settings = get_settings_from_request(request)
    peer_ip = get_client_ip(request, settings.trusted_proxy_ips)
    limiter = get_rate_limiter(request)
    anonymous_rule = RateLimitRule(
        key=f"read:ip:{peer_ip}",
        limit=settings.read_limit,
        window_seconds=settings.read_window_seconds,
    )
    try:
        identity = await authenticate_authorization(
            request.headers.get("authorization"),
            get_session_factory(request),
            settings,
        )
    except AppError as exc:
        if exc.status_code == 401:
            await limiter.enforce([anonymous_rule, invalid_auth_rule(request)])
        raise
    rule = anonymous_rule if identity is None else RateLimitRule(
        key=f"read:user:{identity.user_id}",
        limit=settings.read_limit,
        window_seconds=settings.read_window_seconds,
    )
    await limiter.enforce([rule])
    return identity


ReadIdentity = Annotated[AuthenticatedUser | None, Depends(get_read_identity)]


async def get_authenticated_read_user(
    identity: ReadIdentity,
    _credentials: BearerCredentials = None,
) -> AuthenticatedUser:
    if identity is None:
        raise unauthorized()
    return identity


AuthenticatedReadUser = Annotated[
    AuthenticatedUser, Depends(get_authenticated_read_user)
]
