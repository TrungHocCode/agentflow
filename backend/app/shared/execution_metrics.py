"""Typed, content-free timing records for LLM and tool invocations."""

from datetime import datetime
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field


def strip_internal_execution_metrics(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep benchmark instrumentation out of public API response metadata."""

    public_metadata = dict(metadata or {})
    public_metadata.pop("execution_timings", None)
    public_metadata.pop("execution_metrics", None)
    public_metadata.pop("chat_ttft_samples", None)
    return public_metadata


class ExecutionTiming(BaseModel):
    """One measured LLM or tool call; intentionally excludes prompts and outputs."""

    span_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation: Literal["llm", "tool"]
    phase: Literal["plan", "execute"]
    name: str
    agent_name: str
    task_id: int | None = None
    iteration: int = Field(default=1, ge=1)
    call_id: str | None = None
    model: str | None = None
    duration_ms: float = Field(ge=0)
    status: Literal["success", "failed"]
    result_status: Literal["success", "partial", "failed"] | None = None
    error_type: str | None = None
    started_at: datetime
    completed_at: datetime


def serialize_execution_timings(values: list[Any] | None) -> list[dict[str, Any]]:
    """Convert timing models or dictionaries to JSON-safe dictionaries."""

    serialized: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, ExecutionTiming):
            serialized.append(value.model_dump(mode="json"))
        elif isinstance(value, dict):
            serialized.append(ExecutionTiming.model_validate(value).model_dump(mode="json"))
    return serialized


def merge_execution_timings(
    existing: list[Any] | None,
    incoming: list[Any] | None,
) -> list[dict[str, Any]]:
    """Merge timing spans by span_id so resumed graph state is not double-counted."""

    merged: dict[str, dict[str, Any]] = {}
    for value in serialize_execution_timings(existing) + serialize_execution_timings(incoming):
        merged[value["span_id"]] = value
    return list(merged.values())


def summarize_execution_timings(values: list[Any] | None) -> dict[str, Any]:
    """Summarize measured duration by operation and tool/agent name."""

    spans = serialize_execution_timings(values)
    summary: dict[str, Any] = {
        "llm": {
            "call_count": 0,
            "total_ms": 0.0,
            "max_ms": 0.0,
            "failed_count": 0,
            "by_agent": {},
        },
        "tools": {
            "call_count": 0,
            "total_ms": 0.0,
            "max_ms": 0.0,
            "failed_count": 0,
            "partial_count": 0,
            "by_tool": {},
        },
        "phase_totals_ms": {
            "plan": {"llm_ms": 0.0, "tool_ms": 0.0},
            "execute": {"llm_ms": 0.0, "tool_ms": 0.0},
        },
    }

    for span in spans:
        group_key = "llm" if span["operation"] == "llm" else "tools"
        group = summary[group_key]
        name_key = "agent_name" if group_key == "llm" else "name"
        name = span[name_key]
        detail_key = "by_agent" if group_key == "llm" else "by_tool"
        details = group[detail_key].setdefault(
            name,
            {"call_count": 0, "total_ms": 0.0, "max_ms": 0.0, "failed_count": 0},
        )
        duration = float(span["duration_ms"])
        failed = span["status"] == "failed" or span.get("result_status") == "failed"
        group["call_count"] += 1
        group["total_ms"] += duration
        group["max_ms"] = max(group["max_ms"], duration)
        phase_key = "llm_ms" if group_key == "llm" else "tool_ms"
        summary["phase_totals_ms"][span["phase"]][phase_key] += duration
        details["call_count"] += 1
        details["total_ms"] += duration
        details["max_ms"] = max(details["max_ms"], duration)
        if failed:
            group["failed_count"] += 1
            details["failed_count"] += 1
        if group_key == "tools" and span.get("result_status") == "partial":
            group["partial_count"] += 1
            details.setdefault("partial_count", 0)
            details["partial_count"] += 1

    for group in (summary["llm"], summary["tools"]):
        group["total_ms"] = round(group["total_ms"], 3)
        group["max_ms"] = round(group["max_ms"], 3)
        detail_key = "by_agent" if "by_agent" in group else "by_tool"
        for details in group[detail_key].values():
            details["total_ms"] = round(details["total_ms"], 3)
            details["max_ms"] = round(details["max_ms"], 3)

    summary["total_call_time_ms"] = round(
        summary["llm"]["total_ms"] + summary["tools"]["total_ms"],
        3,
    )
    for phase in summary["phase_totals_ms"].values():
        phase["llm_ms"] = round(phase["llm_ms"], 3)
        phase["tool_ms"] = round(phase["tool_ms"], 3)
        phase["total_ms"] = round(phase["llm_ms"] + phase["tool_ms"], 3)
    return summary
