from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import Settings
from app.core.errors import AppError, unauthorized
from app.core.security import (
    create_access_token,
    hash_password,
    verify_dummy_password,
    verify_password,
)
from app.db import AsyncSessionFactory
from app.models import User
from app.repositories.auth import AuthRepository
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


logger = logging.getLogger(__name__)


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
        except IntegrityError as exc:
            logger.error(
                "Registration constraint failure (SQLSTATE %s)",
                getattr(exc.orig, "sqlstate", None) or "unknown",
            )
            raise AppError(
                status_code=500, code="INTERNAL_ERROR", message="Terjadi kesalahan internal."
            ) from exc
        except SQLAlchemyError as exc:
            raise _database_unavailable() from exc
        if user is None:
            raise AppError(
                status_code=409,
                code="EMAIL_ALREADY_EXISTS",
                message="Email sudah terdaftar.",
            )
        return _public_user(user)

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
