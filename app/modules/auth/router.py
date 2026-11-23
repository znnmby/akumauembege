"""Registration, login, and refresh-session lifecycle endpoints."""

from fastapi import APIRouter, Response, status

from app.core.dependencies import CurrentUser, DbSession
from app.databases.models import User
from app.modules.auth import services
from app.modules.auth.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.modules.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> User:
    return services.register(db, payload)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    return services.login(db, payload)


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    return services.refresh(db, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(payload: RefreshRequest, db: DbSession, user: CurrentUser) -> Response:
    services.logout(db, payload.refresh_token, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
