from __future__ import annotations
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
from app.api.auth import router as auth_router
from app.services.catalog import CatalogLoader, CatalogService
from app.core.config import get_settings
from app.db import create_database, initialize_database
from app.core.errors import register_error_handlers
from app.api.menus import router as menus_router
from app.services.llm import LLMClient
from app.core.rate_limit import RateLimiter, close_redis_client, create_redis_client
from app.api.recommendations import router as recommendations_router


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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
        title="embege",
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

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Literal["ok"]]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(recommendations_router)
    app.include_router(menus_router)
    return app


app = create_app()
