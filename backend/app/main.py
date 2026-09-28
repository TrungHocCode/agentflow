"""FastAPI application entry point and shared HTTP error boundary."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_v1_router
from app.core.config import settings, validate_runtime_settings
from app.core.middleware import RequestBodyLimitMiddleware
from app.db.mongo_client import close_mongo_connection
from app.db.redis_client import close_redis_connection
from app.shared.errors import ApplicationError
from app.shared.observability import RequestCorrelationMiddleware, configure_logging


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.LOG_LEVEL)
    validate_runtime_settings()
    logger.info("AgentFlow API started")
    try:
        yield
    finally:
        await close_mongo_connection()
        await close_redis_connection()
        logger.info("AgentFlow API stopped")


app = FastAPI(
    title="AgentFlow Platform API",
    description="AI Agent Platform Backend API built with Supervisor-Worker architecture.",
    version="1.0.0",
    redirect_slashes=False,
    lifespan=lifespan,
)

app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestCorrelationMiddleware)


def _error_body(
    request: Request,
    *,
    error_id: str,
    code: str,
    category: str,
    message: str,
    retryable: bool,
    entity: str | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "error_id": error_id,
        "code": code,
        "category": category,
        "message": message,
        "retryable": retryable,
        "request_id": getattr(request.state, "request_id", None),
    }
    if entity:
        error["entity"] = entity
    return {"error": error}


@app.exception_handler(ApplicationError)
async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error(
            "Application dependency or persistence failure",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"error_id": exc.error_id, "error_code": exc.code},
        )
    else:
        logger.warning(
            "Application request rejected",
            extra={"error_id": exc.error_id, "error_code": exc.code},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(
            request,
            error_id=exc.error_id,
            code=exc.code,
            category=exc.category,
            message=exc.message,
            retryable=exc.retryable,
            entity=exc.entity,
        ),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    error_id = str(uuid.uuid4())
    # Deliberately omit Pydantic's input values: request data can contain secrets.
    logger.warning(
        "Request validation failed",
        extra={"error_id": error_id, "error_code": "request_validation_error"},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            request,
            error_id=error_id,
            code="request_validation_error",
            category="validation",
            message="The request contains invalid or missing fields.",
            retryable=False,
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    error_id = str(uuid.uuid4())
    status_code = exc.status_code
    category_by_status = {
        401: "authentication",
        403: "authorization",
        404: "not_found",
        409: "conflict",
        422: "validation",
        429: "rate_limit",
    }
    category = category_by_status.get(status_code, "http")
    code = f"http_{status_code}"
    if status_code >= 500:
        category = "internal"
        logger.error(
            "HTTP request failed",
            extra={"error_id": error_id, "error_code": code},
        )
    elif status_code >= 400:
        logger.warning(
            "HTTP request rejected",
            extra={"error_id": error_id, "error_code": code},
        )
    if status_code >= 500:
        message = "The request could not be completed due to a server error."
    else:
        message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    return JSONResponse(
        status_code=status_code,
        content=_error_body(
            request,
            error_id=error_id,
            code=code,
            category=category,
            message=message,
            retryable=status_code == 429 or status_code >= 500,
        ),
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error_id = str(uuid.uuid4())
    logger.error(
        "Unhandled request exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"error_id": error_id, "error_code": "internal_error"},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            request,
            error_id=error_id,
            code="internal_error",
            category="internal",
            message="An unexpected error occurred. Please try again later.",
            retryable=True,
        ),
    )


app.include_router(api_v1_router, prefix="/api")


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AgentFlow Platform API is running", "docs_url": "/docs"}
