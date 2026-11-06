"""Check that the configured local model server can serve the worker model."""

import httpx

from app.core.config import settings
from app.execution.llm import MODEL_NOT_FOUND, MODEL_UNAVAILABLE, MODEL_UNREACHABLE, ollama_base_url
from app.modules.system.health import ModelHealth


class OllamaModelProbe:
    """Report reachability and model presence, never raising.

    Listing installed models cannot predict whether one fits in memory, so a passing
    probe does not guarantee a run: load failures are still reported on the run itself.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, timeout_seconds: float = 3.0) -> None:
        self.base_url = (base_url or ollama_base_url()).rstrip("/")
        self.model = model or settings.LLM_WORKER_MODEL
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    async def check(self) -> ModelHealth:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            installed = {str(item.get("name")) for item in response.json().get("models", [])}
        except httpx.TransportError:
            return ModelHealth(status="unavailable", code=MODEL_UNREACHABLE.code, message=MODEL_UNREACHABLE.message)
        except (httpx.HTTPStatusError, ValueError, AttributeError):
            return ModelHealth(status="unavailable", code=MODEL_UNAVAILABLE.code, message=MODEL_UNAVAILABLE.message)
        if self.model not in installed and f"{self.model}:latest" not in installed:
            return ModelHealth(status="unavailable", code=MODEL_NOT_FOUND.code, message=MODEL_NOT_FOUND.message)
        return ModelHealth(status="ok")
