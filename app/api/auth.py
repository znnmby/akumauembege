from __future__ import annotations

import jwt
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from uuid import UUID
from app.core.config import Settings
from app.db import AsyncSessionFactory
from app.core.errors import AppError, unauthorized
from app.models import AuthSession, User
from app.core.rate_limit import RateLimitRule, RateLimiter, email_rate_limit_digest, get_client_ip
from app.schemas import (
    AuthenticatedUser,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_dummy_password,
    verify_password,
)


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_user(self, *, email: str, password_hash: str) -> User:
        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return await self._session.scalar(select(User).where(User.id == user_id))

    async def get_user_by_email_for_update(self, email: str) -> User | None:
        statement: Select[tuple[User]] = (
            select(User).where(User.email == email).with_for_update()
        )
        return await self._session.scalar(statement)

    async def get_user_by_id_for_update(self, user_id: UUID) -> User | None:
        statement: Select[tuple[User]] = (
            select(User).where(User.id == user_id).with_for_update()
        )
        return await self._session.scalar(statement)

    async def create_auth_session(
        self, *, user_id: UUID, expires_at: datetime
    ) -> AuthSession:
        auth_session = AuthSession(user_id=user_id, expires_at=expires_at)
        self._session.add(auth_session)
        await self._session.flush()
        return auth_session

    async def get_valid_identity(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> AuthenticatedUser | None:
        statement = (
            select(User.email, AuthSession.expires_at)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user_id,
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return AuthenticatedUser(
            user_id=user_id,
            session_id=session_id,
            email=row.email,
            session_expires_at=row.expires_at.astimezone(timezone.utc),
        )

    async def revoke_session(
        self, *, user_id: UUID, session_id: UUID, now: datetime
    ) -> None:
        await self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def revoke_all_user_sessions(
        self, *, user_id: UUID, now: datetime
    ) -> None:
        await self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )


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


def _public_user(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        created_at=user.created_at.astimezone(timezone.utc),
        updated_at=user.updated_at.astimezone(timezone.utc),
    )


def _database_unavailable() -> AppError:
    return AppError(
        status_code=503,
        code="DATABASE_UNAVAILABLE",
        message="Layanan database tidak tersedia.",
    )


class AuthService:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def register(self, data: RegisterRequest) -> UserResponse:
        password_hash = await run_in_threadpool(
            hash_password, data.password.get_secret_value()
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    user = await AuthRepository(session).create_user(
                        email=str(data.email),
                        password_hash=password_hash,
                    )
            return _public_user(user)
        except IntegrityError as exc:
            raise AppError(
                status_code=409,
                code="EMAIL_ALREADY_EXISTS",
                message="Email sudah terdaftar.",
            ) from exc
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc

    async def login(self, data: LoginRequest) -> TokenResponse:
        password = data.password.get_secret_value()
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(
            minutes=self._settings.access_token_expire_minutes
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    repository = AuthRepository(session)
                    # Login and password changes serialize on this row. If login
                    # wins, the subsequent password change revokes its new session;
                    # if the password change wins, the old password no longer verifies.
                    user = await repository.get_user_by_email_for_update(
                        str(data.email)
                    )
                    if user is None:
                        await run_in_threadpool(verify_dummy_password, password)
                        raise AppError(
                            status_code=401,
                            code="INVALID_CREDENTIALS",
                            message="Email atau password tidak valid.",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    valid = await run_in_threadpool(
                        verify_password, password, user.password_hash
                    )
                    if not valid:
                        raise AppError(
                            status_code=401,
                            code="INVALID_CREDENTIALS",
                            message="Email atau password tidak valid.",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    auth_session = await repository.create_auth_session(
                        user_id=user.id,
                        expires_at=expires_at,
                    )
            access_token = create_access_token(
                user_id=user.id,
                session_id=auth_session.id,
                issued_at=issued_at,
                expires_at=expires_at,
                settings=self._settings,
            )
            return TokenResponse(
                access_token=access_token,
                expires_in=self._settings.access_token_expire_minutes * 60,
            )
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc

    async def get_me(self, user_id: UUID) -> UserResponse:
        try:
            async with self._session_factory() as session:
                user = await AuthRepository(session).get_user_by_id(user_id)
            if user is None:
                raise unauthorized()
            return _public_user(user)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc

    async def change_password(
        self,
        *,
        user_id: UUID,
        data: ChangePasswordRequest,
    ) -> None:
        current_password = data.current_password.get_secret_value()
        new_password = data.new_password.get_secret_value()
        now = datetime.now(timezone.utc)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    repository = AuthRepository(session)
                    user = await repository.get_user_by_id_for_update(user_id)
                    if user is None:
                        raise unauthorized()
                    valid = await run_in_threadpool(
                        verify_password, current_password, user.password_hash
                    )
                    if not valid:
                        raise AppError(
                            status_code=400,
                            code="INVALID_CURRENT_PASSWORD",
                            message="Password saat ini tidak valid.",
                        )
                    user.password_hash = await run_in_threadpool(
                        hash_password, new_password
                    )
                    user.updated_at = now
                    await repository.revoke_all_user_sessions(
                        user_id=user_id,
                        now=now,
                    )
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc

    async def logout(self, *, user_id: UUID, session_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await AuthRepository(session).revoke_session(
                        user_id=user_id,
                        session_id=session_id,
                        now=now,
                    )
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc


router = APIRouter(prefix="/auth", tags=["auth"])


def _service(request: Request) -> AuthService:
    return AuthService(
        session_factory=get_session_factory(request),
        settings=get_settings_from_request(request),
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(request: Request, body: RegisterRequest) -> UserResponse:
    settings = get_settings_from_request(request)
    client_ip = get_client_ip(request, settings.trusted_proxy_ips)
    await get_rate_limiter(request).enforce(
        [
            RateLimitRule(
                key=f"register:ip:{client_ip}",
                limit=settings.register_limit,
                window_seconds=settings.register_window_seconds,
            )
        ]
    )
    return await _service(request).register(body)


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest) -> TokenResponse:
    settings = get_settings_from_request(request)
    client_ip = get_client_ip(request, settings.trusted_proxy_ips)
    email_digest = email_rate_limit_digest(str(body.email), settings)
    await get_rate_limiter(request).enforce(
        [
            RateLimitRule(
                key=f"login:ip:{client_ip}",
                limit=settings.login_limit,
                window_seconds=settings.login_window_seconds,
            ),
            RateLimitRule(
                key=f"login:email:{email_digest}",
                limit=settings.login_limit,
                window_seconds=settings.login_window_seconds,
            ),
        ]
    )
    return await _service(request).login(body)


@router.get("/me", response_model=UserResponse)
async def me(request: Request, current_user: CurrentUser) -> UserResponse:
    settings = get_settings_from_request(request)
    await get_rate_limiter(request).enforce(
        [
            RateLimitRule(
                key=f"me:user:{current_user.user_id}",
                limit=settings.me_limit,
                window_seconds=settings.me_window_seconds,
            )
        ]
    )
    return await _service(request).get_me(current_user.user_id)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: CurrentUser,
) -> Response:
    settings = get_settings_from_request(request)
    await get_rate_limiter(request).enforce(
        [
            RateLimitRule(
                key=f"change-password:user:{current_user.user_id}",
                limit=settings.change_password_limit,
                window_seconds=settings.change_password_window_seconds,
            )
        ]
    )
    await _service(request).change_password(
        user_id=current_user.user_id,
        data=body,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def logout(request: Request, current_user: CurrentUser) -> Response:
    # Logout intentionally has no Redis dependency so a user can always revoke
    # the active session while the limiter is unavailable.
    await _service(request).logout(
        user_id=current_user.user_id,
        session_id=current_user.session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
