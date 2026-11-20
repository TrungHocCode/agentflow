import os
from dataclasses import dataclass
from typing import Optional

import httpx
from langchain_core.language_models import BaseChatModel

from app.core.config import settings
from app.execution.model_router import InferencePurpose, model_name_for
from app.shared.errors import ApplicationError


@dataclass(frozen=True)
class ModelFailure:
    """A safe, actionable explanation of why the local model could not answer."""

    code: str
    message: str


MODEL_OUT_OF_MEMORY = ModelFailure(
    "model_out_of_memory",
    "The local model could not load because this machine is out of memory. "
    "Close other applications or configure a smaller model, then run again.",
)
MODEL_NOT_FOUND = ModelFailure(
    "model_not_found",
    "The configured model is not available on the local model server. Install it or change the model setting, then run again.",
)
MODEL_UNREACHABLE = ModelFailure(
    "model_unreachable",
    "The local model server could not be reached. Start it and run again.",
)
MODEL_UNAVAILABLE = ModelFailure(
    "model_unavailable",
    "The local model could not answer. Check the model server and run again.",
)
_OUT_OF_MEMORY_MARKERS = ("out of memory", "out-of-memory", "failed to allocate", "unable to allocate",
                          "requires more system memory", "requires more memory")


def classify_model_failure(exc: BaseException) -> ModelFailure | None:
    """Recognize model-server failures anywhere in an exception chain; raw text is never surfaced."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # Application errors wrap other dependencies, such as the database.
        if isinstance(current, ApplicationError):
            return None
        text = str(current).lower()
        # Matched by name so this module keeps its lazy dependency on the Ollama client.
        if type(current).__name__ == "ResponseError" and type(current).__module__.startswith("ollama"):
            if any(marker in text for marker in _OUT_OF_MEMORY_MARKERS):
                return MODEL_OUT_OF_MEMORY
            if getattr(current, "status_code", None) == 404 or "not found" in text:
                return MODEL_NOT_FOUND
            return MODEL_UNAVAILABLE
        # llama.cpp is reached through the OpenAI client; same name-matching to stay lazy.
        name = type(current).__name__
        if type(current).__module__.startswith("openai") and name in ("NotFoundError", "InternalServerError"):
            if any(marker in text for marker in _OUT_OF_MEMORY_MARKERS):
                return MODEL_OUT_OF_MEMORY
            return MODEL_NOT_FOUND if name == "NotFoundError" else MODEL_UNAVAILABLE
        if isinstance(current, (ConnectionError, httpx.ConnectError, httpx.ConnectTimeout)):
            return MODEL_UNREACHABLE
        # The server was reached but did not answer, so "start it" would be the wrong advice.
        if isinstance(current, httpx.TransportError):
            return MODEL_UNAVAILABLE
        current = current.__cause__ or current.__context__
    return None


def ollama_base_url() -> str:
    """Resolve the Ollama address; the worker and the availability probe must agree."""

    return os.getenv("OLLAMA_BASE_URL") or settings.LLM_BASE_URL


def model_server_base_url() -> str:
    """Resolve the address of the configured provider's server."""

    return settings.LLAMACPP_BASE_URL if settings.LLM_PROVIDER == "llamacpp" else ollama_base_url()


def get_llm(
    purpose: InferencePurpose | str = InferencePurpose.PLANNER,
    temperature: float = 0.7,
    base_url: Optional[str] = None,
    model_name_override: Optional[str] = None,
) -> BaseChatModel:
    """
    Instantiate the chat model assigned to an internal inference purpose on the configured provider.
    """
    model = model_name_override or model_name_for(purpose)
    url = base_url or model_server_base_url()

    if settings.LLM_PROVIDER == "llamacpp":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai is required for llama.cpp execution. Install via: pip install langchain-openai")
        # llama-server serves one model and ignores the name; thinking is a per-request template kwarg.
        extra_body = None
        if settings.LLM_THINKING is not None:
            extra_body = {"chat_template_kwargs": {"enable_thinking": settings.LLM_THINKING}}
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            base_url=url,
            api_key=settings.LLAMACPP_API_KEY,
            max_tokens=settings.LLM_OUTPUT_TOKENS,
            extra_body=extra_body,
        )

    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        raise ImportError("langchain-ollama is required for live Ollama execution. Install via: pip install langchain-ollama")

    return ChatOllama(
        model=model,
        temperature=temperature,
        base_url=url,
        num_ctx=settings.LLM_CONTEXT_TOKENS,
        num_predict=settings.LLM_OUTPUT_TOKENS,
        reasoning=settings.LLM_THINKING,
    )
