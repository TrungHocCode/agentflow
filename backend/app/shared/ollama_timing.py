"""Content-free timing for raw Ollama response chunks in a conversation turn."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Mapping


def _milliseconds(seconds: float) -> float:
    return round(seconds * 1000, 3)


def _nanoseconds_to_milliseconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value / 1_000_000, 3)
    return None


@dataclass
class OllamaRequestTiming:
    """Observe stream timing without retaining prompts, thinking, or answers."""

    model: str
    started_at: float = field(repr=False)
    request_offset_ms: float
    first_chunk_ms: float | None = None
    raw_ttft_ms: float | None = None
    first_thinking_ms: float | None = None
    first_content_ms: float | None = None
    elapsed_ms: float | None = None
    server_total_ms: float | None = None
    load_ms: float | None = None
    prompt_eval_ms: float | None = None
    eval_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    status: str = "running"

    def observe(self, chunk: Mapping[str, Any] | str) -> None:
        """Timestamp the first raw payload, including thinking hidden by LangChain."""

        elapsed_ms = _milliseconds(perf_counter() - self.started_at)
        if self.first_chunk_ms is None:
            self.first_chunk_ms = elapsed_ms
        if isinstance(chunk, str):
            return

        message = chunk.get("message") or {}
        thinking = message.get("thinking") or ""
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if thinking and self.first_thinking_ms is None:
            self.first_thinking_ms = elapsed_ms
        if content and self.first_content_ms is None:
            self.first_content_ms = elapsed_ms
        if (thinking or content or tool_calls) and self.raw_ttft_ms is None:
            self.raw_ttft_ms = elapsed_ms

        if chunk.get("done"):
            self.status = "completed"
            self.elapsed_ms = elapsed_ms
            self.server_total_ms = _nanoseconds_to_milliseconds(chunk.get("total_duration"))
            self.load_ms = _nanoseconds_to_milliseconds(chunk.get("load_duration"))
            self.prompt_eval_ms = _nanoseconds_to_milliseconds(chunk.get("prompt_eval_duration"))
            self.eval_ms = _nanoseconds_to_milliseconds(chunk.get("eval_duration"))
            self.prompt_eval_count = chunk.get("prompt_eval_count")
            self.eval_count = chunk.get("eval_count")

    def finish(self, *, failed: bool = False) -> None:
        if self.elapsed_ms is None:
            self.elapsed_ms = _milliseconds(perf_counter() - self.started_at)
            self.status = "failed" if failed else "interrupted"

    def snapshot(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "request_offset_ms": self.request_offset_ms,
            "first_chunk_ms": self.first_chunk_ms,
            "raw_ttft_ms": self.raw_ttft_ms,
            "first_thinking_ms": self.first_thinking_ms,
            "first_content_ms": self.first_content_ms,
            "elapsed_ms": self.elapsed_ms,
            "server_total_ms": self.server_total_ms,
            "load_ms": self.load_ms,
            "prompt_eval_ms": self.prompt_eval_ms,
            "eval_ms": self.eval_ms,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "status": self.status,
        }


@dataclass
class OllamaTurnTiming:
    turn_id: str
    turn_started_at: float = field(repr=False)
    requests: list[OllamaRequestTiming] = field(default_factory=list)

    def begin_request(self, model: str) -> OllamaRequestTiming:
        started_at = perf_counter()
        timing = OllamaRequestTiming(
            model=model,
            started_at=started_at,
            request_offset_ms=_milliseconds(started_at - self.turn_started_at),
        )
        self.requests.append(timing)
        return timing

    def snapshot(self) -> list[dict[str, Any]]:
        return [request.snapshot() for request in self.requests]


active_ollama_turn: ContextVar[OllamaTurnTiming | None] = ContextVar(
    "active_ollama_turn", default=None
)
