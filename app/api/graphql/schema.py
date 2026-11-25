"""Query-only Strawberry schema and guarded FastAPI HTTP transport."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

import strawberry
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
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types

from app.api.graphql.context import build_graphql_context
from app.core.config import Settings
from app.core.exceptions import AppError
from app.modules.catalog.graphql.queries import CatalogQuery
from app.modules.recommendations.graphql.queries import RecommendationQuery

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
        if not _is_included(getattr(selection, "directives", ()), variables):
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
