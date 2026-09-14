"""Application service for operational health reporting."""

from typing import Any, Dict, Protocol


class HealthProbe(Protocol):
    async def check(self) -> Dict[str, Any]:
        ...


class HealthService:
    """Expose infrastructure health without coupling the API to DB clients."""

    def __init__(self, probe: HealthProbe) -> None:
        self.probe = probe

    async def get_status(self) -> Dict[str, Any]:
        return await self.probe.check()
