"""Operational health endpoint."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_health_service
from app.modules.system.health import HealthService

router = APIRouter()

@router.get("/health")
async def health_check(service: HealthService = Depends(get_health_service)):
    """Return dependency status without exposing infrastructure details to routing."""

    return await service.get_status()
