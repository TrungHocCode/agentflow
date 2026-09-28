"""Bounded task-level timings for workflow run diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskExecutionMetric(BaseModel):
    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: int
    node: str
    status: Literal["done", "partial", "failed"]
    duration_ms: float = Field(ge=0)
    started_at: datetime
    completed_at: datetime


def serialize_task_execution_metrics(values: list[Any] | None) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, TaskExecutionMetric):
            metrics.append(value.model_dump(mode="json"))
        elif isinstance(value, dict):
            metrics.append(TaskExecutionMetric.model_validate(value).model_dump(mode="json"))
    return metrics


def merge_task_execution_metrics(
    existing: list[Any] | None,
    incoming: list[Any] | None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for metric in serialize_task_execution_metrics(existing) + serialize_task_execution_metrics(incoming):
        merged[metric["attempt_id"]] = metric
    return list(merged.values())


def summarize_task_execution_metrics(
    values: list[Any] | None,
    dependencies: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    metrics = serialize_task_execution_metrics(values)
    duration_by_task: dict[int, float] = {}
    for metric in metrics:
        task_id = int(metric["task_id"])
        duration_by_task[task_id] = duration_by_task.get(task_id, 0.0) + float(metric["duration_ms"])

    longest_path: dict[int, float] = {}
    visiting: set[int] = set()

    def path_duration(task_id: int) -> float:
        if task_id in longest_path:
            return longest_path[task_id]
        if task_id in visiting:
            return 0.0
        visiting.add(task_id)
        parent_path = max(
            (path_duration(parent) for parent in (dependencies or {}).get(task_id, [])),
            default=0.0,
        )
        visiting.remove(task_id)
        longest_path[task_id] = parent_path + duration_by_task.get(task_id, 0.0)
        return longest_path[task_id]

    for task_id in duration_by_task:
        path_duration(task_id)

    durations = [float(metric["duration_ms"]) for metric in metrics]
    return {
        "attempt_count": len(metrics),
        "task_count": len(duration_by_task),
        "done_count": sum(metric["status"] == "done" for metric in metrics),
        "partial_count": sum(metric["status"] == "partial" for metric in metrics),
        "failed_count": sum(metric["status"] == "failed" for metric in metrics),
        "total_task_ms": round(sum(durations), 3),
        "max_task_ms": round(max(durations), 3) if durations else 0.0,
        "critical_path_ms": round(max(longest_path.values(), default=0.0), 3),
    }
