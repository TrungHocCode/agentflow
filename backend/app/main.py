from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.v1 import api_v1_router
from app.db.mongo_client import close_mongo_connection
from app.db.redis_client import close_redis_connection
from app.shared.errors import ApplicationError, PersistenceError, ValidationError
from app.core.config import settings

app = FastAPI(
    title="AgentFlow Platform API",
    description="AI Agent Platform Backend API built with Supervisor-Worker architecture.",
    version="1.0.0",
    redirect_slashes=False,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApplicationError)
async def application_error_handler(
    request: Request,
    exc: ApplicationError,
) -> JSONResponse:
    """Translate application errors into consistent HTTP responses."""

    del request
    if isinstance(exc, PersistenceError):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(exc, ValidationError):
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )

# Register API Router
app.include_router(api_v1_router, prefix="/api")

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()
    await close_redis_connection()

@app.get("/")
async def root():
    return {"message": "AgentFlow Platform API is running", "docs_url": "/docs"}
