"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.dependencies import get_auth_service, get_current_user_id
from app.modules.identity.models import AuthResponse, LoginRequest, RegisterRequest, UserRecord
from app.modules.identity.service import IdentityService


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    service: IdentityService = Depends(get_auth_service),
):
    return await service.register(request)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    service: IdentityService = Depends(get_auth_service),
):
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
