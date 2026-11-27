"""Public schema exports; implementations are grouped by feature."""

from app.schemas.auth import (
    AuthenticatedUser,
    ChangePasswordRequest,
    JWTClaims,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.catalog import CatalogDocument, CatalogMenu, MenuSnapshot
from app.schemas.llm import LLMCandidate, LLMSelectionRequest, LLMSelectionResponse
from app.schemas.pagination import PaginatedResponse
from app.schemas.recommendations import (
    RecommendationCreateRequest,
    RecommendationResponse,
)

__all__ = [
    "AuthenticatedUser",
    "CatalogDocument",
    "CatalogMenu",
    "ChangePasswordRequest",
    "JWTClaims",
    "LLMCandidate",
    "LLMSelectionRequest",
    "LLMSelectionResponse",
    "LoginRequest",
    "MenuSnapshot",
    "PaginatedResponse",
    "RecommendationCreateRequest",
    "RecommendationResponse",
    "RegisterRequest",
    "TokenResponse",
    "UserResponse",
]
