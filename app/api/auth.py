from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.dependencies import (
    CurrentUser,
    get_rate_limiter,
    get_session_factory,
    get_settings_from_request,
)
from app.core.errors import error_responses
from app.core.rate_limit import RateLimitRule, email_rate_limit_digest, get_client_ip
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


def _service(request: Request) -> AuthService:
    return AuthService(
        session_factory=get_session_factory(request),
        settings=get_settings_from_request(request),
    )


@router.post(
    "/register",
    responses=error_responses(409, 422, 429, 500, 503),
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


@router.post(
    "/login",
    response_model=TokenResponse,
    responses=error_responses(401, 422, 429, 500, 503),
)
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


@router.get(
    "/me",
    response_model=UserResponse,
    responses=error_responses(401, 429, 500, 503),
)
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
    responses=error_responses(400, 401, 422, 429, 500, 503),
    response_model=ChangePasswordResponse,
)
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: CurrentUser,
) -> ChangePasswordResponse:
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
    return ChangePasswordResponse(message="Password changed successfully.")


@router.post(
    "/logout",
    responses=error_responses(401, 429, 500, 503),
    response_model=LogoutResponse,
)
async def logout(request: Request, current_user: CurrentUser) -> LogoutResponse:
    # A valid session can be revoked even when Redis is unavailable.
    await _service(request).logout(
        user_id=current_user.user_id,
        session_id=current_user.session_id,
    )
    return LogoutResponse(message="Logout successful.")
