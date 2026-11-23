import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.dependencies import DbSession
from app.core.errors import fail
from app.modules.auth.router import router as auth_router
from app.modules.categories.router import router as categories_router
from app.modules.menu_items.router import router as menu_items_router
from app.modules.orders.router import router as orders_router
from app.modules.realtime.router import router as realtime_router
from app.modules.users.router import router as users_router

app_name = os.environ["APP_NAME"]
logger = logging.getLogger(app_name)
app = FastAPI(title=app_name, version="1.0.0")

for router in (
    auth_router,
    users_router,
    categories_router,
    menu_items_router,
    orders_router,
    realtime_router,
):
    app.include_router(router)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        body = {**exc.detail, "status_code": exc.status_code}
    else:
        body = {
            "message": str(exc.detail),
            "code": "HTTP_ERROR",
            "status_code": exc.status_code,
        }
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    fields = [".".join(map(str, error["loc"])) for error in exc.errors()]
    return JSONResponse(
        {
            "message": "Cek input la, jangan kaya prabowo. Periksa tipe, format, dan batas nilai.",
            "code": "VALIDATION_ERROR",
            "status_code": 400,
            "fields": fields,
        },
        status_code=400,
    )


@app.exception_handler(Exception)
async def internal_error(_request: Request, exc: Exception) -> JSONResponse:
    # Never log SQL parameters, passwords, tokens, or provider response bodies.
    logger.error("Unhandled error: %s", type(exc).__name__)
    return JSONResponse(
        {
            "message": "Internal eror bosq.",
            "code": "INTERNAL_ERROR",
            "status_code": 500,
        },
        status_code=500,
    )


@app.middleware("http")
async def request_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": f"{app.title} API"}


@app.get("/health", tags=["Health"])
def health(db: DbSession) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        fail(503, "Database tidak tersedia.", "DATABASE_UNAVAILABLE")
    return {"status": "ok", "database": "ok"}