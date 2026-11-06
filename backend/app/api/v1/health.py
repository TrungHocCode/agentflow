"""Operational health endpoint."""

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_health_service, get_model_health_probe
from app.modules.system.health import HealthService, ModelHealth, ModelHealthProbe

router = APIRouter()

@router.get("/health")
async def health_check(service: HealthService = Depends(get_health_service)):
    """Return dependency status without exposing infrastructure details to routing."""

    return await service.get_status()


@router.get("/health/live")
async def liveness_check():
    """Process-level liveness probe; it intentionally does not check dependencies."""

    return {"status": "ok"}


@router.get("/health/model", response_model=ModelHealth)
async def model_check(probe: ModelHealthProbe = Depends(get_model_health_probe)) -> ModelHealth:
    """Report model availability separately: losing it must not fail API readiness."""

    return await probe.check()


@router.get("/health/ready")
async def readiness_check(
    response: Response,
    service: HealthService = Depends(get_health_service),
):
    """Dependency readiness probe for Docker or a reverse proxy."""

    report = await service.get_status()
    if report.get("status") != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
