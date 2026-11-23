import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

app_name = os.environ["APP_NAME"]
logger = logging.getLogger(app_name)
app = FastAPI(title=app_name, version="1.0.0")


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
async def health() -> dict[str, str]:
    return {"status": "ok"}