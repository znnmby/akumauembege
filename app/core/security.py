"""Authentication and security utilities."""

from datetime import datetime, timedelta, timezone

from uuid import uuid4
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("dummy-password-for-constant-verification")


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
            "type": "access",
            "jti": uuid4().hex,
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    claims = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "iat", "exp", "type", "jti"]},
    )
    if (
        claims["type"] != "access"
        or not isinstance(claims["sub"], str)
        or not claims["sub"].isdigit()
        or int(claims["sub"]) <= 0
    ):
        raise jwt.InvalidTokenError("Invalid access token claims")
    return claims
