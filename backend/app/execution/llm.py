import os
from typing import Optional

from langchain_core.language_models import BaseChatModel

from app.core.config import settings
from app.execution.model_router import InferencePurpose, model_name_for


def get_llm(
    purpose: InferencePurpose | str = InferencePurpose.PLANNER,
    temperature: float = 0.7,
    base_url: Optional[str] = None
) -> BaseChatModel:
    """
    Instantiate the ChatOllama model assigned to an internal inference purpose.
    """
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        raise ImportError("langchain-ollama is required for live Ollama execution. Install via: pip install langchain-ollama")

    model = model_name_for(purpose)
    url = base_url or os.getenv("OLLAMA_BASE_URL") or settings.LLM_BASE_URL

    return ChatOllama(
        model=model,
        temperature=temperature,
        base_url=url
    )
