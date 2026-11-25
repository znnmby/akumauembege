"""PostgreSQL access for users and authentication sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, User
from app.modules.auth.schemas import AuthenticatedUser


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
