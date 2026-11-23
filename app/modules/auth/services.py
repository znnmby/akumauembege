"""Password authentication and single-use, persisted refresh sessions."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import fail
from app.core.security import DUMMY_HASH, create_access_token, verify_password
from app.databases.models import RefreshToken, User
from app.modules.auth.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.modules.users.services import create_user


def register(db: Session, payload: RegisterRequest) -> User:
    return create_user(db, payload, payload.role)


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _new_token_pair(db: Session, user_id: int) -> TokenResponse:
    settings = get_settings()
    refresh_token = token_urlsafe(48)
    db.add(
        RefreshToken(
            token_hash=_token_hash(refresh_token),
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


def _commit_tokens(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "The session could not be saved.", "SESSION_CONFLICT")


def login(db: Session, payload: LoginRequest) -> TokenResponse:
    # Existing account names remain case-sensitive; email identifiers do not.
    user = db.scalar(
        select(User)
        .where(
            or_(
                User.user_name == payload.identifier,
                func.lower(User.email) == payload.identifier.lower(),
            )
        )
        .order_by(case((User.user_name == payload.identifier, 0), else_=1), User.id)
        .limit(1)
    )
    password_valid = verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_valid:
        fail(401, "Invalid identifier or password.", "INVALID_CREDENTIALS")
    response = _new_token_pair(db, user.id)
    _commit_tokens(db)
    return response


def _locked_token(db: Session, raw_token: str, user_id: int | None = None) -> RefreshToken:
    statement = select(RefreshToken).where(RefreshToken.token_hash == _token_hash(raw_token))
    if user_id is not None:
        statement = statement.where(RefreshToken.user_id == user_id)
    token = db.scalar(statement.with_for_update().execution_options(populate_existing=True))
    now = datetime.now(timezone.utc)
    expires_at = token.expires_at if token is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if token is None or token.revoked_at is not None or expires_at <= now:
        db.rollback()
        fail(401, "Invalid or expired refresh token.", "INVALID_REFRESH_TOKEN")
    return token


def _consume_token(db: Session, token: RefreshToken) -> None:
    now = datetime.now(timezone.utc)
    # The conditional write also protects against reuse on engines without row locks.
    result = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token.token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        fail(401, "Invalid or expired refresh token.", "INVALID_REFRESH_TOKEN")


def refresh(db: Session, raw_token: str) -> TokenResponse:
    token = _locked_token(db, raw_token)
    if db.get(User, token.user_id) is None:
        db.rollback()
        fail(401, "Invalid or expired refresh token.", "INVALID_REFRESH_TOKEN")
    _consume_token(db, token)
    response = _new_token_pair(db, token.user_id)
    _commit_tokens(db)
    return response


def logout(db: Session, raw_token: str, user_id: int) -> None:
    token = _locked_token(db, raw_token, user_id)
    _consume_token(db, token)
    _commit_tokens(db)
