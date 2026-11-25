from __future__ import annotations

import base64
import binascii
import json
import logging
import strawberry
from dataclasses import dataclass
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from graphql import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLSyntaxError,
    InlineFragmentNode,
    OperationDefinitionNode,
    get_operation_ast,
    parse,
    validate,
)
from graphql.execution.values import get_variable_values
from graphql.language.ast import DirectiveNode, SelectionSetNode
from graphql.utilities import value_from_ast_untyped
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
from strawberry.types import Info
from typing import Any, Iterable, NoReturn, TYPE_CHECKING
from uuid import UUID
from app.api.auth import authenticate_authorization
from app.services.catalog import CatalogService
from app.core.config import Settings
from app.core.errors import AppError
from app.models import Recommendation
from app.core.rate_limit import RateLimitRule, get_client_ip
from app.api.recommendations import RecommendationRepository
from app.schemas import AuthenticatedUser, CatalogMenu as CatalogMenuModel, MenuSnapshot


_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048


@strawberry.type
class PageInfo:
    """Relay-style page metadata without exposing storage details."""

    end_cursor: str | None = strawberry.field(name="endCursor")
    has_next_page: bool = strawberry.field(name="hasNextPage")


def bad_user_input(message: str) -> NoReturn:
    """Raise a consistently coded GraphQL input error."""

    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT"})

def validate_first(first: int, *, maximum: int) -> int:
    if isinstance(first, bool) or not isinstance(first, int) or not 1 <= first <= maximum:
        bad_user_input(f"first harus antara 1 dan {maximum}.")
    return first

def normalize_search(search: str | None) -> str | None:
    if search is None:
        return None
    if not isinstance(search, str):
        bad_user_input("search harus berupa string.")
    normalized = search.strip()
    if len(normalized) > 150:
        bad_user_input("Panjang search maksimal 150 karakter.")
    return normalized or None


def encode_menu_cursor(last_id: str, search: str | None) -> str:
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "kind": "menu",
            "last_id": last_id,
            "search": normalize_search(search),
        }
    )


def decode_menu_cursor(cursor: str | None, search: str | None) -> str | None:
    if cursor is None:
        return None
    payload = _decode(cursor)
    if set(payload) != {"v", "kind", "last_id", "search"}:
        bad_user_input("Cursor menu tidak valid.")
    if payload.get("v") != _CURSOR_VERSION or payload.get("kind") != "menu":
        bad_user_input("Cursor menu tidak valid.")
    last_id = payload.get("last_id")
    stored_search = payload.get("search")
    if (
        not isinstance(last_id, str)
        or not 1 <= len(last_id) <= 64
        or not all(
            character.isascii()
            and (character.isalnum() or character in "_-")
            for character in last_id
        )
    ):
        bad_user_input("Cursor menu tidak valid.")
    if stored_search is not None and not isinstance(stored_search, str):
        bad_user_input("Cursor menu tidak valid.")
    if stored_search != normalize_search(search):
        bad_user_input("Cursor tidak sesuai dengan konteks pencarian.")
    return last_id


def encode_history_cursor(created_at: datetime, recommendation_id: UUID, user_id: UUID) -> str:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    timestamp = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "kind": "history",
            "created_at": timestamp,
            "id": str(recommendation_id),
            "user_id": str(user_id),
        }
    )


def decode_history_cursor(
    cursor: str | None,
    user_id: UUID,
) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    payload = _decode(cursor)
    if set(payload) != {"v", "kind", "created_at", "id", "user_id"}:
        bad_user_input("Cursor riwayat tidak valid.")
    if payload.get("v") != _CURSOR_VERSION or payload.get("kind") != "history":
        bad_user_input("Cursor riwayat tidak valid.")
    if payload.get("user_id") != str(user_id):
        bad_user_input("Cursor tidak sesuai dengan pengguna aktif.")
    created_at_value = payload.get("created_at")
    recommendation_id_value = payload.get("id")
    if not isinstance(created_at_value, str) or not isinstance(recommendation_id_value, str):
        bad_user_input("Cursor riwayat tidak valid.")
    try:
        created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
        recommendation_id = UUID(recommendation_id_value)
    except (ValueError, TypeError):
        bad_user_input("Cursor riwayat tidak valid.")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        bad_user_input("Cursor riwayat tidak valid.")
    return created_at.astimezone(timezone.utc), recommendation_id


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode(cursor: str) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        bad_user_input("Cursor tidak valid.")
    if not all(
        character.isascii()
        and (character.isalnum() or character in "_-")
        for character in cursor
    ):
        bad_user_input("Cursor tidak valid.")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        if len(raw) > _MAX_CURSOR_LENGTH:
            raise ValueError("cursor payload too large")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        bad_user_input("Cursor tidak valid.")
    if not isinstance(payload, dict):
        bad_user_input("Cursor tidak valid.")
    return payload


if TYPE_CHECKING:
    from app.core.rate_limit import RateLimiter


@dataclass(frozen=True, slots=True)
class GraphQLContext:
    request: Request
    settings: Settings
    authenticated_user: AuthenticatedUser | None
    catalog_service: CatalogService
    session_factory: async_sessionmaker[AsyncSession]
    recommendation_repository: RecommendationRepository


async def build_graphql_context(request: Request) -> GraphQLContext:
    """Authenticate an optional bearer token and account for one GraphQL request."""

    settings: Settings = request.app.state.settings
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    rate_limiter: RateLimiter = request.app.state.rate_limiter
    peer_ip = get_client_ip(request, settings.trusted_proxy_ips)
    try:
        authenticated_user = await authenticate_authorization(
            request.headers.get("Authorization"),
            session_factory,
            settings,
            required=False,
        )
    except AppError:
        # Invalid/revoked bearer traffic is still request-level traffic. Count
        # it by peer identity before returning UNAUTHENTICATED.
        await rate_limiter.enforce(
            [
                RateLimitRule(
                    key=f"graphql:ip:{peer_ip}",
                    limit=settings.graphql_limit,
                    window_seconds=settings.graphql_window_seconds,
                )
            ]
        )
        raise

    if authenticated_user is not None:
        identity = f"user:{authenticated_user.user_id}"
    else:
        identity = f"ip:{peer_ip}"
    await rate_limiter.enforce(
        [
            RateLimitRule(
                key=f"graphql:{identity}",
                limit=settings.graphql_limit,
                window_seconds=settings.graphql_window_seconds,
            )
        ]
    )

    return GraphQLContext(
        request=request,
        settings=settings,
        authenticated_user=authenticated_user,
        catalog_service=request.app.state.catalog_service,
        session_factory=session_factory,
        recommendation_repository=RecommendationRepository(),
    )


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


@strawberry.type(name="Menu")
class Menu:
    id: strawberry.ID
    name: str
    description: str
    ascii_art: list[str] = strawberry.field(name="asciiArt")

    @classmethod
    def from_model(cls, menu: CatalogMenuModel) -> "Menu":
        return cls(
            id=strawberry.ID(menu.id),
            name=menu.name,
            description=menu.description,
            ascii_art=list(menu.ascii_art),
        )


@strawberry.type(name="MenuEdge")
class MenuEdge:
    cursor: str
    node: Menu


@strawberry.type(name="MenuConnection")
class MenuConnection:
    edges: list[MenuEdge]
    page_info: PageInfo = strawberry.field(name="pageInfo")


@strawberry.type(name="MenuSnapshot")
class MenuSnapshotType:
    id: strawberry.ID
    name: str
    description: str
    ascii_art: list[str] = strawberry.field(name="asciiArt")

    @classmethod
    def from_snapshot(cls, snapshot: MenuSnapshot) -> "MenuSnapshotType":
        return cls(
            id=strawberry.ID(snapshot.id),
            name=snapshot.name,
            description=snapshot.description,
            ascii_art=list(snapshot.ascii_art),
        )


@strawberry.type(name="Recommendation")
class RecommendationType:
    id: strawberry.ID
    region_name: str = strawberry.field(name="regionName")
    geographic_description: str = strawberry.field(name="geographicDescription")
    selection_reason: str = strawberry.field(name="selectionReason")
    menu: MenuSnapshotType
    created_at: datetime = strawberry.field(name="createdAt")

    @classmethod
    def from_record(cls, record: Recommendation) -> "RecommendationType":
        snapshot = MenuSnapshot.model_validate(record.menu_snapshot)
        return cls(
            id=strawberry.ID(str(record.id)),
            region_name=record.region_name,
            geographic_description=record.geographic_description,
            selection_reason=record.selection_reason,
            menu=MenuSnapshotType.from_snapshot(snapshot),
            created_at=record.created_at.astimezone(timezone.utc),
        )


@strawberry.type(name="RecommendationEdge")
class RecommendationEdge:
    cursor: str
    node: RecommendationType


@strawberry.type(name="RecommendationConnection")
class RecommendationConnection:
    edges: list[RecommendationEdge]
    page_info: PageInfo = strawberry.field(name="pageInfo")


def _bad_user_input(message: str) -> GraphQLError:
    return GraphQLError(message, extensions={"code": "BAD_USER_INPUT"})


def _catalog_service(info: Info) -> CatalogService:
    service = getattr(info.context, "catalog_service", None)
    if not isinstance(service, CatalogService):
        raise GraphQLError(
            "Layanan katalog tidak tersedia.",
            extensions={"code": "SERVICE_UNAVAILABLE"},
        )
    return service


@strawberry.type
class CatalogQuery:
    @strawberry.field
    def menus(
        self,
        info: Info,
        search: str | None = None,
        first: int = 20,
        after: str | None = None,
    ) -> MenuConnection:
        settings = getattr(info.context, "settings", None)
        max_page_size = min(getattr(settings, "graphql_max_page_size", 100), 100)
        if isinstance(first, bool) or not 1 <= first <= max_page_size:
            raise _bad_user_input(f"first harus berada di antara 1 dan {max_page_size}.")

        service = _catalog_service(info)
        try:
            normalized_search = service.normalize_search(search)
        except ValueError as exc:
            raise _bad_user_input(str(exc)) from None

        last_id = decode_menu_cursor(after, normalized_search)
        try:
            page = service.list_active_menus(
                normalized_search,
                first=first,
                after_id=last_id,
            )
        except ValueError as exc:
            raise _bad_user_input(str(exc)) from None

        edges = [
            MenuEdge(
                cursor=encode_menu_cursor(menu.id, normalized_search),
                node=Menu.from_model(menu),
            )
            for menu in page.items
        ]
        return MenuConnection(
            edges=edges,
            page_info=PageInfo(
                end_cursor=edges[-1].cursor if edges else None,
                has_next_page=page.has_next_page,
            ),
        )

    @strawberry.field
    def menu(self, info: Info, id: strawberry.ID) -> Menu | None:
        catalog_menu = _catalog_service(info).get_active_menu(str(id))
        return None if catalog_menu is None else Menu.from_model(catalog_menu)


@strawberry.type
class RecommendationQuery:
    @strawberry.field(name="myHistory")
    async def my_history(
        self,
        info: Info[GraphQLContext, None],
        first: int = 20,
        after: str | None = None,
    ) -> RecommendationConnection:
        context = info.context
        authenticated_user = require_authenticated(context)
        page_size = validate_first(first, maximum=context.settings.graphql_max_page_size)
        cursor = decode_history_cursor(after, authenticated_user.user_id)
        try:
            async with context.session_factory() as session:
                records, has_next_page = await context.recommendation_repository.list_for_user(
                    session,
                    user_id=authenticated_user.user_id,
                    first=page_size,
                    after=cursor,
                )
        except SQLAlchemyError:
            raise GraphQLError(
                "Layanan riwayat tidak tersedia.",
                extensions={"code": "SERVICE_UNAVAILABLE"},
            ) from None

        edges = [
            RecommendationEdge(
                cursor=encode_history_cursor(record.created_at, record.id, authenticated_user.user_id),
                node=RecommendationType.from_record(record),
            )
            for record in records
        ]
        return RecommendationConnection(
            edges=edges,
            page_info=PageInfo(
                end_cursor=edges[-1].cursor if edges else None,
                has_next_page=has_next_page,
            ),
        )

    @strawberry.field(name="myRecommendation")
    async def my_recommendation(
        self,
        info: Info[GraphQLContext, None],
        id: strawberry.ID,
    ) -> RecommendationType | None:
        context = info.context
        authenticated_user = require_authenticated(context)
        try:
            recommendation_id = UUID(str(id))
        except (TypeError, ValueError):
            bad_user_input("ID rekomendasi bukan UUID yang valid.")

        try:
            async with context.session_factory() as session:
                record = await context.recommendation_repository.get_for_user(
                    session,
                    recommendation_id=recommendation_id,
                    user_id=authenticated_user.user_id,
                )
        except SQLAlchemyError:
            raise GraphQLError(
                "Layanan riwayat tidak tersedia.",
                extensions={"code": "SERVICE_UNAVAILABLE"},
            ) from None
        return None if record is None else RecommendationType.from_record(record)


logger = logging.getLogger(__name__)

Query = merge_types("Query", (CatalogQuery, RecommendationQuery))
graphql_schema = strawberry.Schema(query=Query)

_PUBLIC_ERROR_CODES = {
    "BAD_USER_INPUT",
    "INTERNAL_SERVER_ERROR",
    "SERVICE_UNAVAILABLE",
    "UNAUTHENTICATED",
}


@dataclass(frozen=True, slots=True)
class _Metrics:
    depth: int
    complexity: int


class _QueryLimitError(ValueError):
    pass


def create_graphql_router(settings: Settings) -> APIRouter:
    """Create an HTTP-only router; no mutation, subscription, or batching path exists."""

    router = APIRouter()
    ide_renderer = GraphQLRouter(
        graphql_schema,
        graphql_ide="graphiql",
        allow_queries_via_get=False,
    )

    @router.get("/graphql", include_in_schema=False)
    async def graphql_ide(request: Request) -> Response:
        if not settings.graphql_ide_enabled:
            return Response(status_code=404)
        return await ide_renderer.render_graphql_ide(request)

    @router.post("/graphql", include_in_schema=False)
    async def graphql_endpoint(request: Request) -> JSONResponse:
        try:
            context = await build_graphql_context(request)
        except AppError as error:
            return _app_error_response(error)

        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _request_error("Body GraphQL harus berupa JSON object.")
        if isinstance(payload, list):
            return _request_error("Batch GraphQL tidak didukung.")
        if not isinstance(payload, dict):
            return _request_error("Body GraphQL harus berupa JSON object.")

        query = payload.get("query")
        variables = payload.get("variables")
        operation_name = payload.get("operationName")
        if not isinstance(query, str) or not query:
            return _request_error("Field query wajib berupa string non-kosong.")
        if variables is not None and not isinstance(variables, dict):
            return _request_error("Field variables harus berupa object atau null.")
        if operation_name is not None and not isinstance(operation_name, str):
            return _request_error("Field operationName harus berupa string atau null.")

        if len(query) > settings.graphql_max_query_length:
            return _request_error("Dokumen GraphQL melebihi batas panjang.")

        prepared = _prepare_operation(
            query=query,
            variables=variables or {},
            operation_name=operation_name,
            settings=settings,
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        result = await graphql_schema.execute(
            query,
            variable_values=prepared,
            context_value=context,
            operation_name=operation_name,
        )
        body: dict[str, Any] = {"data": result.data}
        if result.errors:
            body["errors"] = [_format_execution_error(error) for error in result.errors]
        if result.extensions:
            body["extensions"] = result.extensions
        return JSONResponse(body, status_code=200)

    return router


def _prepare_operation(
    *,
    query: str,
    variables: dict[str, Any],
    operation_name: str | None,
    settings: Settings,
) -> dict[str, Any] | JSONResponse:
    try:
        document = parse(query)
    except GraphQLSyntaxError as error:
        return _request_errors([error], "Sintaks GraphQL tidak valid.")

    validation_errors = validate(graphql_schema._schema, document)
    if validation_errors:
        return _request_errors(validation_errors, "Dokumen GraphQL tidak valid.")

    operation = get_operation_ast(document, operation_name)
    if operation is None:
        return _request_error("Operasi GraphQL tidak ditemukan atau ambigu.")

    coerced_result = get_variable_values(
        graphql_schema._schema,
        operation.variable_definitions or (),
        variables,
        max_errors=20,
    )
    if isinstance(coerced_result, list):
        return _request_errors(coerced_result, "Variabel GraphQL tidak valid.")

    # graphql-core 3.2 returns the coerced dict directly, while newer
    # releases wrap it with source metadata in a VariableValues object.
    coerced = getattr(coerced_result, "coerced", coerced_result)
    if not isinstance(coerced, dict):
        return _request_error("Variabel GraphQL tidak dapat diproses.")

    try:
        metrics = _operation_metrics(
            document,
            operation,
            coerced,
            max_page_size=settings.graphql_max_page_size,
        )
    except _QueryLimitError as error:
        return _request_error(str(error))
    if metrics.depth > settings.graphql_max_depth:
        return _request_error("Kedalaman query GraphQL melebihi batas.")
    if metrics.complexity > settings.graphql_max_complexity:
        return _request_error("Kompleksitas query GraphQL melebihi batas.")
    return coerced


def _operation_metrics(
    document: DocumentNode,
    operation: OperationDefinitionNode,
    variables: dict[str, Any],
    *,
    max_page_size: int,
) -> _Metrics:
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    return _selection_metrics(
        operation.selection_set,
        fragments=fragments,
        variables=variables,
        current_depth=0,
        fragment_stack=frozenset(),
        max_page_size=max_page_size,
    )


def _selection_metrics(
    selection_set: SelectionSetNode,
    *,
    fragments: dict[str, FragmentDefinitionNode],
    variables: dict[str, Any],
    current_depth: int,
    fragment_stack: frozenset[str],
    max_page_size: int,
) -> _Metrics:
    maximum_depth = current_depth
    complexity = 0
    for selection in selection_set.selections:
        if not _is_included(getattr(selection, "directives", None) or (), variables):
            continue
        if isinstance(selection, FieldNode):
            field_depth = current_depth + 1
            maximum_depth = max(maximum_depth, field_depth)
            child_complexity = 0
            if selection.selection_set is not None:
                child = _selection_metrics(
                    selection.selection_set,
                    fragments=fragments,
                    variables=variables,
                    current_depth=field_depth,
                    fragment_stack=fragment_stack,
                    max_page_size=max_page_size,
                )
                maximum_depth = max(maximum_depth, child.depth)
                child_complexity = child.complexity
            multiplier = 1
            if selection.name.value in {"menus", "myHistory"}:
                multiplier = _connection_size(selection, variables, max_page_size)
            complexity += 1 + (child_complexity * multiplier)
            continue

        if isinstance(selection, InlineFragmentNode):
            nested = _selection_metrics(
                selection.selection_set,
                fragments=fragments,
                variables=variables,
                current_depth=current_depth,
                fragment_stack=fragment_stack,
                max_page_size=max_page_size,
            )
            maximum_depth = max(maximum_depth, nested.depth)
            complexity += nested.complexity
            continue

        if isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            if name in fragment_stack:
                raise _QueryLimitError("Siklus fragment GraphQL tidak valid.")
            fragment = fragments.get(name)
            if fragment is None:
                raise _QueryLimitError("Fragment GraphQL tidak ditemukan.")
            nested = _selection_metrics(
                fragment.selection_set,
                fragments=fragments,
                variables=variables,
                current_depth=current_depth,
                fragment_stack=fragment_stack | {name},
                max_page_size=max_page_size,
            )
            maximum_depth = max(maximum_depth, nested.depth)
            complexity += nested.complexity
    return _Metrics(depth=maximum_depth, complexity=complexity)


def _connection_size(
    field: FieldNode,
    variables: dict[str, Any],
    maximum: int,
) -> int:
    size: Any = 20
    for argument in field.arguments or ():
        if argument.name.value == "first":
            size = value_from_ast_untyped(argument.value, variables)
            break
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= maximum:
        raise _QueryLimitError(f"first harus antara 1 dan {maximum}.")
    return size


def _is_included(directives: Iterable[DirectiveNode], variables: dict[str, Any]) -> bool:
    """Honor resolved skip/include directives; unknown values are included conservatively."""

    for directive in directives:
        if directive.name.value not in {"skip", "include"}:
            continue
        condition: Any = None
        for argument in directive.arguments or ():
            if argument.name.value == "if":
                condition = value_from_ast_untyped(argument.value, variables)
                break
        if directive.name.value == "skip" and condition is True:
            return False
        if directive.name.value == "include" and condition is False:
            return False
    return True


def _request_error(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "data": None,
            "errors": [{"message": message, "extensions": {"code": "BAD_USER_INPUT"}}],
        },
        status_code=400,
    )


def _request_errors(
    errors: Iterable[GraphQLError], message: str
) -> JSONResponse:
    formatted: list[dict[str, Any]] = []
    for error in errors:
        item: dict[str, Any] = {
            "message": message,
            "extensions": {"code": "BAD_USER_INPUT"},
        }
        if error.locations:
            item["locations"] = [
                {"line": location.line, "column": location.column}
                for location in error.locations
            ]
        formatted.append(item)
    return JSONResponse({"data": None, "errors": formatted}, status_code=400)


def _app_error_response(error: AppError) -> JSONResponse:
    if error.status_code == 401:
        code = "UNAUTHENTICATED"
        status_code = 200
    elif error.status_code == 429:
        code = "RATE_LIMIT_EXCEEDED"
        status_code = 429
    elif error.status_code in {502, 503, 504}:
        code = "SERVICE_UNAVAILABLE"
        status_code = error.status_code
    else:
        code = "INTERNAL_SERVER_ERROR"
        status_code = 500
    extensions: dict[str, Any] = {"code": code}
    if error.retry_after is not None:
        extensions["retryAfter"] = error.retry_after
    return JSONResponse(
        {
            "data": None,
            "errors": [{"message": error.message, "extensions": extensions}],
        },
        status_code=status_code,
        headers=error.headers,
    )


def _format_execution_error(error: GraphQLError) -> dict[str, Any]:
    code = (error.extensions or {}).get("code")
    if code not in _PUBLIC_ERROR_CODES:
        logger.error("Unexpected GraphQL resolver error: %s", type(error.original_error).__name__)
        code = "INTERNAL_SERVER_ERROR"
        message = "Terjadi kesalahan internal."
    else:
        message = error.message
    formatted: dict[str, Any] = {"message": message, "extensions": {"code": code}}
    if error.locations:
        formatted["locations"] = [
            {"line": location.line, "column": location.column}
            for location in error.locations
        ]
    if error.path:
        formatted["path"] = error.path
    return formatted
