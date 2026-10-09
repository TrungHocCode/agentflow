"""Run lifecycle observer ports; domain hooks plug in without importing domain modules."""

from __future__ import annotations

from typing import Protocol

from app.execution.state import Task
from app.modules.runs.models import RunDocument


class TaskCompletedObserver(Protocol):
    """Called after a task reaches a terminal state; may return follow-up tasks to merge."""

    async def on_task_completed(self, run: RunDocument, task: Task, status: str) -> list[Task]: ...


class RunCompletedObserver(Protocol):
    """Called once a run reaches completed; must never rewrite the terminal state."""

    async def on_run_completed(self, run: RunDocument) -> None: ...
