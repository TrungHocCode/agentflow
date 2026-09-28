"""Content-free measurements for one streamed chat-model invocation."""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, Field


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


class LLMCallMetric(BaseModel):
    """One LLM request, with optional provider statistics and no prompt/output text."""

    call_id: str
    component: str
    purpose: str
    model: str | None = None
    task_id: int | None = None
    iteration: int = Field(default=1, ge=1)
    status: Literal["success", "failed"]
    error_type: str | None = None
    started_at: datetime
    completed_at: datetime
    request_latency_ms: float = Field(ge=0)
    raw_ttft_ms: float | None = Field(default=None, ge=0)
    generation_ms: float | None = Field(default=None, ge=0)
    itl_p50_ms: float | None = Field(default=None, ge=0)
    itl_p95_ms: float | None = Field(default=None, ge=0)
    itl_max_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    output_tokens_per_second: float | None = Field(default=None, ge=0)
    model_load_ms: float | None = Field(default=None, ge=0)
    prompt_eval_ms: float | None = Field(default=None, ge=0)
    provider_total_ms: float | None = Field(default=None, ge=0)


class LLMCallObserver(AsyncCallbackHandler):
    """Observe LangChain token callbacks and final provider usage metadata."""

    def __init__(
        self,
        *,
        call_id: str,
        component: str,
        purpose: str,
        model: str | None,
        task_id: int | None = None,
        iteration: int = 1,
    ) -> None:
        self.call_id = call_id
        self.component = component
        self.purpose = purpose
        self.model = model
        self.task_id = task_id
        self.iteration = iteration
        self.started_at = datetime.now(timezone.utc)
        self._started = perf_counter()
        self._token_times: list[float] = []
        self._provider_values: dict[str, float | int] = {}
        self._usage: dict[str, int] = {}

    async def on_llm_new_token(self, token: Any, **kwargs: Any) -> None:
        """Capture arrival times only; never retain generated token content."""

        chunk = kwargs.get("chunk")
        message = getattr(chunk, "message", None)
        tool_chunks = getattr(message, "tool_call_chunks", None) if message else None
        has_content = bool(token) or bool(tool_chunks)
        if has_content:
            self._token_times.append(perf_counter())

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        generation = self._first_generation(response)
        if generation is None:
            return

        message = getattr(generation, "message", None)
        usage = getattr(message, "usage_metadata", None) or {}
        if isinstance(usage, dict):
            self._copy_count(usage, "input_tokens", "input_tokens")
            self._copy_count(usage, "output_tokens", "output_tokens")

        info = getattr(generation, "generation_info", None) or {}
        if not isinstance(info, dict):
            return
        for source, target in (
            ("total_duration", "provider_total_ms"),
            ("load_duration", "model_load_ms"),
            ("prompt_eval_duration", "prompt_eval_ms"),
            ("eval_duration", "generation_ms"),
        ):
            value = info.get(source)
            if isinstance(value, (int, float)) and value >= 0:
                # Ollama reports these durations in nanoseconds.
                self._provider_values[target] = round(float(value) / 1_000_000, 3)
        self._copy_count(info, "prompt_eval_count", "input_tokens")
        self._copy_count(info, "eval_count", "output_tokens")

    def to_metric(
        self,
        *,
        status: Literal["success", "failed"],
        error_type: str | None = None,
    ) -> LLMCallMetric:
        completed_at = datetime.now(timezone.utc)
        request_latency_ms = round((perf_counter() - self._started) * 1000, 3)
        intervals = [
            (right - left) * 1000
            for left, right in zip(self._token_times, self._token_times[1:])
        ]
        raw_ttft_ms = (
            round((self._token_times[0] - self._started) * 1000, 3)
            if self._token_times
            else None
        )
        generation_ms = self._provider_values.get("generation_ms")
        if generation_ms is None and len(self._token_times) > 1:
            generation_ms = round(
                (self._token_times[-1] - self._token_times[0]) * 1000,
                3,
            )
        output_tokens = self._usage.get("output_tokens")
        output_tokens_per_second = None
        if output_tokens is not None and generation_ms and generation_ms > 0:
            output_tokens_per_second = round(output_tokens * 1000 / generation_ms, 3)

        return LLMCallMetric(
            call_id=self.call_id,
            component=self.component,
            purpose=self.purpose,
            model=self.model,
            task_id=self.task_id,
            iteration=self.iteration,
            status=status,
            error_type=error_type,
            started_at=self.started_at,
            completed_at=completed_at,
            request_latency_ms=request_latency_ms,
            raw_ttft_ms=raw_ttft_ms,
            generation_ms=generation_ms,
            itl_p50_ms=_percentile(intervals, 0.5),
            itl_p95_ms=_percentile(intervals, 0.95),
            itl_max_ms=round(max(intervals), 3) if intervals else None,
            input_tokens=self._usage.get("input_tokens"),
            output_tokens=output_tokens,
            output_tokens_per_second=output_tokens_per_second,
            model_load_ms=self._provider_values.get("model_load_ms"),
            prompt_eval_ms=self._provider_values.get("prompt_eval_ms"),
            provider_total_ms=self._provider_values.get("provider_total_ms"),
        )

    def _copy_count(self, source: dict[str, Any], key: str, target: str) -> None:
        value = source.get(key)
        if isinstance(value, int) and value >= 0:
            self._usage[target] = value

    @staticmethod
    def _first_generation(response: LLMResult) -> Any | None:
        if not response.generations or not response.generations[0]:
            return None
        return response.generations[0][0]


def serialize_llm_call_metrics(values: list[Any] | None) -> list[dict[str, Any]]:
    """Validate metrics and convert them to JSON-safe dictionaries."""

    serialized: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, LLMCallMetric):
            serialized.append(value.model_dump(mode="json"))
        elif isinstance(value, dict):
            serialized.append(LLMCallMetric.model_validate(value).model_dump(mode="json"))
    return serialized


def merge_llm_call_metrics(
    existing: list[Any] | None,
    incoming: list[Any] | None,
) -> list[dict[str, Any]]:
    """Deduplicate metrics by call ID when a workflow state is replayed/resumed."""

    merged: dict[str, dict[str, Any]] = {}
    for metric in serialize_llm_call_metrics(existing) + serialize_llm_call_metrics(incoming):
        merged[metric["call_id"]] = metric
    return list(merged.values())


def summarize_llm_call_metrics(values: list[Any] | None) -> dict[str, Any]:
    """Create a compact per-run summary; unavailable provider values remain absent."""

    calls = serialize_llm_call_metrics(values)
    succeeded = [call for call in calls if call["status"] == "success"]
    summary: dict[str, Any] = {
        "call_count": len(calls),
        "failed_count": len(calls) - len(succeeded),
        "request_latency_p50_ms": _percentile(
            [float(call["request_latency_ms"]) for call in calls], 0.5
        ),
        "request_latency_p95_ms": _percentile(
            [float(call["request_latency_ms"]) for call in calls], 0.95
        ),
        "raw_ttft_p50_ms": _percentile(
            [float(call["raw_ttft_ms"]) for call in calls if call["raw_ttft_ms"] is not None], 0.5
        ),
        "raw_ttft_p95_ms": _percentile(
            [float(call["raw_ttft_ms"]) for call in calls if call["raw_ttft_ms"] is not None], 0.95
        ),
        "itl_p50_ms": _percentile(
            [float(call["itl_p50_ms"]) for call in calls if call["itl_p50_ms"] is not None], 0.5
        ),
        "itl_p95_ms": _percentile(
            [float(call["itl_p95_ms"]) for call in calls if call["itl_p95_ms"] is not None], 0.95
        ),
        "input_tokens": sum(call["input_tokens"] or 0 for call in calls),
        "output_tokens": sum(call["output_tokens"] or 0 for call in calls),
        "generation_ms": round(
            sum(float(call["generation_ms"] or 0) for call in calls), 3
        ),
        "model_load_ms": round(
            sum(float(call["model_load_ms"] or 0) for call in calls), 3
        ),
        "prompt_eval_ms": round(
            sum(float(call["prompt_eval_ms"] or 0) for call in calls), 3
        ),
    }
    generation_ms = summary["generation_ms"]
    if generation_ms > 0 and any(call["output_tokens"] is not None for call in calls):
        summary["output_tokens_per_second"] = round(
            summary["output_tokens"] * 1000 / generation_ms,
            3,
        )
    return summary
