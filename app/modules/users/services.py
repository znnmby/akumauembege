"""Database-backed account creation and administrator listing."""

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import fail
from app.core.schemas import paginate
from app.core.security import hash_password
from app.databases.models import Role, User
from app.modules.users.schemas import UserCreateBase


def create_user(db: Session, payload: UserCreateBase, role: str) -> User:
    if db.get(Role, role) is None:
        fail(409, "The requested role is not configured.", "ROLE_NOT_CONFIGURED")
    existing_id = db.scalar(
        select(User.id)
        .where(
            or_(
                User.user_name == payload.user_name,
                func.lower(User.email) == str(payload.email),
            )
        )
        .limit(1)
    )
    if existing_id is not None:
        fail(409, "The user name or email is already registered.", "USER_CONFLICT")
    user = User(
        user_name=payload.user_name,
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "The user name or email is already registered, or the role is unavailable.", "USER_CONFLICT")
    db.refresh(user)
    return user


def list_users(db: Session, page: int, limit: int) -> dict:
    return paginate(db, select(User).order_by(User.id), page, limit)
