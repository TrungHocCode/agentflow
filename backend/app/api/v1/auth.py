"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.dependencies import get_auth_service, get_current_user_id, get_rate_limiter
from app.core.config import settings
from app.modules.identity.models import AuthResponse, LoginRequest, RegisterRequest, UserRecord
from app.modules.identity.service import IdentityService
from app.modules.system.ports import RateLimiter


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    http_request: Request,
    service: IdentityService = Depends(get_auth_service),
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    await limiter.consume(
        "register-ip",
        http_request.client.host if http_request.client else "unknown",
        settings.LOGIN_RATE_LIMIT_PER_MINUTE,
        60,
    )
    return await service.register(request)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    service: IdentityService = Depends(get_auth_service),
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    client_host = http_request.client.host if http_request.client else "unknown"
    await limiter.consume(
        "login-ip",
        client_host,
        settings.LOGIN_RATE_LIMIT_PER_MINUTE,
        60,
    )
    await limiter.consume(
        "login-account",
        f"{client_host}:{str(request.email).lower()}",
        settings.LOGIN_RATE_LIMIT_PER_MINUTE,
        60,
    )
    return await service.login(request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(_: str = Depends(get_current_user_id)):
    """JWT logout is client-side token disposal for the MVP."""
    return None


@router.get("/me", response_model=UserRecord)
async def current_user(
    authorization: str | None = Header(None),
    service: IdentityService = Depends(get_auth_service),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return await service.current_user(authorization.split(" ", 1)[1].strip())
