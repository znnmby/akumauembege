"""Embege ASGI application and dependency lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.graphql.schema import create_graphql_router
from app.api.rest.router import router as rest_router
from app.core.config import get_settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter
from app.db.init_db import initialize_database
from app.db.session import create_database
from app.integrations.llm.client import LLMClient
from app.integrations.redis import close_redis_client, create_redis_client
from app.modules.catalog.loader import CatalogLoader
from app.modules.catalog.service import CatalogService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    loader = CatalogLoader(settings.catalog_path)
    loader.load()
    catalog_service = CatalogService(loader)

    async with AsyncExitStack() as cleanup:
        engine, session_factory = create_database(settings)
        cleanup.push_async_callback(engine.dispose)
        await initialize_database(engine)

        redis_client = create_redis_client(settings)
        cleanup.push_async_callback(close_redis_client, redis_client)
        await redis_client.ping()
        rate_limiter = RateLimiter(redis_client, settings)

        llm_client = LLMClient(settings)
        cleanup.push_async_callback(llm_client.close)

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.catalog_service = catalog_service
        app.state.redis = redis_client
        app.state.rate_limiter = rate_limiter
        app.state.llm_client = llm_client

        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Embege",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_error_handlers(app)

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(rest_router)
    app.include_router(create_graphql_router(settings))
    return app


app = create_app()
