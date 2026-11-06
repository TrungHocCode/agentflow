"""Application service for operational health reporting."""

from typing import Any, Dict, Literal, Protocol

from pydantic import BaseModel


class ModelHealth(BaseModel):
    """Whether the worker model can be expected to serve a run."""

    status: Literal["ok", "unavailable"]
    code: str | None = None
    message: str | None = None


class HealthProbe(Protocol):
    async def check(self) -> Dict[str, Any]:
        ...


class ModelHealthProbe(Protocol):
    async def check(self) -> ModelHealth:
        ...


class HealthService:
    """Expose infrastructure health without coupling the API to DB clients."""

    def __init__(self, probe: HealthProbe) -> None:
        self.probe = probe

    async def get_status(self) -> Dict[str, Any]:
        return await self.probe.check()
