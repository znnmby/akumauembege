"""GraphQL authentication and sanitized domain-error mapping."""

from __future__ import annotations

from typing import NoReturn

from graphql import GraphQLError

from app.api.graphql.context import GraphQLContext
from app.core.exceptions import AppError
from app.modules.auth.schemas import AuthenticatedUser


def require_authenticated(context: GraphQLContext) -> AuthenticatedUser:
    if context.authenticated_user is None:
        raise GraphQLError(
            "Autentikasi diperlukan.",
            extensions={"code": "UNAUTHENTICATED"},
        )
    return context.authenticated_user


def raise_graphql_app_error(error: AppError) -> NoReturn:
    if error.status_code == 401:
        code = "UNAUTHENTICATED"
    elif error.status_code in {400, 422}:
        code = "BAD_USER_INPUT"
    elif error.status_code in {429, 502, 503, 504}:
        code = "SERVICE_UNAVAILABLE"
    else:
        code = "INTERNAL_SERVER_ERROR"
    raise GraphQLError(error.message, extensions={"code": code}) from None
