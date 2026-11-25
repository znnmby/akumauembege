"""Request-scoped dependency resolution and session-backed authentication."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, unauthorized
from app.core.rate_limit import RateLimiter
from app.core.security import decode_access_token
from app.db.session import AsyncSessionFactory
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import AuthenticatedUser


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


async def authenticate_authorization(
    authorization: str | None,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    *,
    required: bool = False,
) -> AuthenticatedUser | None:
    """Authenticate an optional raw Authorization header.

    An absent optional header maps to anonymous. Any supplied malformed,
    invalid, expired, or revoked credential is rejected rather than ignored.
    """

    if authorization is None:
        if required:
            raise unauthorized()
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise unauthorized()
    try:
        claims = decode_access_token(parts[1], settings)
        user_id = UUID(claims.sub)
        session_id = UUID(claims.sid)
    except (jwt.InvalidTokenError, ValueError):
        raise unauthorized() from None
    try:
        async with session_factory() as session:
            identity = await AuthRepository(session).get_valid_identity(
                user_id=user_id,
                session_id=session_id,
                now=datetime.now(timezone.utc),
            )
    except SQLAlchemyError as exc:
        raise AppError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Layanan database tidak tersedia.",
        ) from exc
    if identity is None:
        raise unauthorized()
    return identity


async def get_current_user(request: Request) -> AuthenticatedUser:
    identity = await authenticate_authorization(
        request.headers.get("authorization"),
        get_session_factory(request),
        get_settings_from_request(request),
        required=True,
    )
    if identity is None:  # Required mode guarantees this; keeps the type explicit.
        raise unauthorized()
    return identity


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
