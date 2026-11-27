from __future__ import annotations

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from datetime import datetime, timezone
from pydantic import ValidationError
from uuid import UUID
from app.core.config import Settings
from app.schemas.auth import JWTClaims


_password_hasher = PasswordHasher()
_DUMMY_HASH = _password_hasher.hash("embege-dummy-password-not-used")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    """Spend Argon2 work for an unknown account without exposing the result."""

    verify_password(password, _DUMMY_HASH)


def create_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    issued_at: datetime,
    expires_at: datetime,
    settings: Settings,
) -> str:
    issued_at = issued_at.astimezone(timezone.utc)
    expires_at = expires_at.astimezone(timezone.utc)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> JWTClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={
                "require": ["sub", "sid", "iat", "exp"],
                "verify_signature": True,
                "verify_iat": True,
                "verify_exp": True,
            },
        )
        return JWTClaims.model_validate(payload, strict=True)
    except (jwt.InvalidTokenError, ValidationError, TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("Invalid access token") from exc
