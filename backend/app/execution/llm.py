import os
from typing import Optional
from langchain_core.language_models import BaseChatModel
def get_llm(
    model_name: Optional[str] = None,
    temperature: float = 0.7,
    base_url: Optional[str] = None
) -> BaseChatModel:
    """
    Factory function to instantiate a ChatOllama LLM model.
    Defaults to OLLAMA_MODEL env var or 'qwen3:8b' if unspecified.
    """
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        raise ImportError("langchain-ollama is required for live Ollama execution. Install via: pip install langchain-ollama")

    model = model_name or os.getenv("OLLAMA_MODEL", "qwen3:8b")
    url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    return ChatOllama(
        model=model,
        temperature=temperature,
        base_url=url
    )

