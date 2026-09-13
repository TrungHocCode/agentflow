"""Application service for run lifecycle and execution progress."""

import json
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from app.execution.ports import ExecutionPort
from app.execution.state import State, Task
from app.modules.runs.models import RunDocument
from app.modules.runs.ports import RunRepository
from app.modules.workflows.ports import WorkflowRepository
from app.shared.events import ExecutionEvent


class RunService:
    """Coordinates run use cases through persistence and execution ports.

    The service deliberately does not know whether a run is stored in MongoDB,
    memory, or another database, nor whether execution is performed by the
    current in-process LangGraph adapter or a future background worker.
    """

    def __init__(
        self,
        run_repository: RunRepository,
        workflow_repository: WorkflowRepository | None,
        execution_port: ExecutionPort,
    ) -> None:
        self.run_repository = run_repository
        self.workflow_repository = workflow_repository
        self.execution_port = execution_port

    async def save_run_doc(self, document: RunDocument) -> None:
        """Persist the latest run snapshot through the repository port."""

        await self.run_repository.save(document)

    async def get_run(self, run_id: str) -> RunDocument | None:
        """Load one run without exposing the underlying document store."""

        return await self.run_repository.get(run_id)

    async def list_runs(
        self,
        flow_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[RunDocument]:
        """List run history, optionally filtered by workflow."""

        return await self.run_repository.list(flow_id=flow_id, limit=limit)

    async def create_run(
        self,
        flow_id: str,
        input_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RunDocument:
        """Create a pending run and build a plan when the workflow has none."""

        run_id = str(uuid.uuid4())
        plan = await self._load_workflow_plan(flow_id)
        logs = [f"[RunService] Initialized Run {run_id} for Flow {flow_id}."]
        if input_message:
            logs.append(f"[User Input]: {input_message}")

        mode = "conversation"
        result_storage: List[Dict[str, Any]] = []

        if not plan:
            try:
                initial_state: State = {
                    "messages": [input_message] if input_message else [],
                    "plan": [],
                    "current_task": None,
                    "logs": logs,
                    "result_storage": [],
                    "mode": "conversation",
                    "metadata": metadata or {},
                }
                result_state = await self.execution_port.create_plan(run_id, initial_state)
                plan = self._normalize_tasks(result_state.get("plan") or [])
                mode = result_state.get("mode", "conversation")
                logs = result_state.get("logs") or logs
                result_storage = result_state.get("result_storage") or []
            except Exception as exc:
                logs.append(f"[RunService Warning] Plan creation error: {exc}")

        document = RunDocument(
            run_id=run_id,
            flow_id=flow_id,
            status="pending",
            mode=mode,
            plan=plan,
            logs=logs,
            result_storage=result_storage,
            metadata=metadata or {},
        )
        await self.save_run_doc(document)
        return document

    async def send_message(
        self,
        run_id: str,
        message: str,
    ) -> Optional[RunDocument]:
        """Resume the build-phase conversation for a run."""

        run_doc = await self.get_run(run_id)
        if not run_doc:
            return None

        if run_doc.status in ("completed", "failed"):
            return run_doc

        try:
            result_state = await self.execution_port.continue_conversation(run_id, message)
            run_doc.plan = self._normalize_tasks(
                result_state.get("plan") or run_doc.plan
            )
            run_doc.mode = result_state.get("mode", run_doc.mode)
            run_doc.logs.extend(result_state.get("logs") or [])
            run_doc.logs.append(f"[User Message]: {message}")
            run_doc.updated_at = datetime.utcnow()
        except Exception as exc:
            run_doc.logs.append(f"[RunService Warning] send_message error: {exc}")

        await self.save_run_doc(run_doc)
        return run_doc

    async def approve_run(
        self,
        run_id: str,
        approved: bool = True,
        feedback: Optional[str] = None,
    ) -> Optional[RunDocument]:
        """Approve or reject a proposed plan without blocking the HTTP request."""

        run_doc = await self.get_run(run_id)
        if not run_doc:
            return None

        if not approved:
            run_doc.logs.append(
                f"[User Approval]: Plan rejected. Feedback: {feedback or 'None'}"
            )
            run_doc.status = "failed"
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            return run_doc

        run_doc.logs.append(
            f"[User Approval]: Plan approved. Feedback: {feedback or 'None'}"
        )
        run_doc.status = "running"
        run_doc.updated_at = datetime.utcnow()
        await self.save_run_doc(run_doc)
        return run_doc

    async def stream_run_events(self, run_id: str) -> AsyncGenerator[str, None]:
        """Stream run progress using a stable, frontend-compatible SSE envelope."""

        run_doc = await self.get_run(run_id)
        if not run_doc:
            yield self._sse_event(
                run_id,
                "error",
                phase="run",
                status="failed",
                message=f"Run {run_id} not found",
            )
            return

        yield self._sse_event(
            run_id,
            "start",
            phase="run",
            status=run_doc.status,
        )

        if run_doc.status == "pending":
            plan_data = [self._task_data(task) for task in run_doc.plan]
            yield self._sse_event(
                run_id,
                "plan_ready",
                phase="build",
                status="pending",
                payload={"plan": plan_data},
                plan=plan_data,
            )
            yield self._sse_event(
                run_id,
                "completed",
                phase="run",
                status="pending",
            )
            return

        if run_doc.status in ("completed", "failed"):
            for log in run_doc.logs:
                yield self._sse_event(
                    run_id,
                    "log",
                    phase="execute",
                    status=run_doc.status,
                    message=str(log),
                )
            yield self._sse_event(
                run_id,
                "completed",
                phase="run",
                status=run_doc.status,
            )
            return

        try:
            yield self._sse_event(
                run_id,
                "log",
                phase="execute",
                status="running",
                message=(
                    f"[ExecutionEngine] Resuming graph for Run {run_id} "
                    "in mode: executing..."
                ),
            )

            async for chunk in self.execution_port.stream_execution(run_id):
                for node_name, node_output in chunk.items():
                    if not isinstance(node_output, dict):
                        continue

                    logs = node_output.get("logs") or []
                    for log in logs:
                        run_doc.logs.append(log)
                        yield self._sse_event(
                            run_id,
                            "log",
                            phase="execute",
                            status="running",
                            label=node_name,
                            message=str(log),
                            payload={"node": node_name},
                        )

                    current_task = node_output.get("current_task")
                    if current_task:
                        current_task = self._normalize_task(current_task)
                        run_doc.current_task = current_task
                        task_data = self._task_data(current_task)
                        yield self._sse_event(
                            run_id,
                            "task_update",
                            phase="execute",
                            status="running",
                            task_id=str(current_task.id),
                            label=node_name,
                            payload={"task": task_data},
                            task=task_data,
                        )

                    plan = node_output.get("plan")
                    if plan:
                        run_doc.plan = self._merge_plan(run_doc.plan, plan)
                        plan_data = [self._task_data(task) for task in run_doc.plan]
                        yield self._sse_event(
                            run_id,
                            "plan_update",
                            phase="execute",
                            status="running",
                            label=node_name,
                            payload={"plan": plan_data},
                            plan=plan_data,
                        )

                    result_storage = node_output.get("result_storage")
                    if result_storage:
                        run_doc.result_storage.extend(result_storage)
                        yield self._sse_event(
                            run_id,
                            "results_update",
                            phase="execute",
                            status="running",
                            label=node_name,
                            payload={"results": run_doc.result_storage},
                            results=run_doc.result_storage,
                        )

                    run_doc.updated_at = datetime.utcnow()
                    await self.save_run_doc(run_doc)

            all_finished = bool(run_doc.plan) and all(
                task.status in ("done", "failed", "skipped")
                for task in run_doc.plan
            )
            run_doc.status = "completed" if all_finished else "running"
            run_doc.logs.append(
                f"[RunService] Run {run_id} execution completed with status: "
                f"{run_doc.status}"
            )
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)

            yield self._sse_event(
                run_id,
                "completed",
                phase="run",
                status=run_doc.status,
                payload={
                    "plan": [self._task_data(task) for task in run_doc.plan],
                    "results": run_doc.result_storage,
                },
                plan=[self._task_data(task) for task in run_doc.plan],
                results=run_doc.result_storage,
            )
        except Exception as exc:
            run_doc.status = "failed"
            error_message = f"[RunService Error]: {exc}"
            run_doc.logs.append(error_message)
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            yield self._sse_event(
                run_id,
                "error",
                phase="execute",
                status="failed",
                message=error_message,
            )
            yield self._sse_event(
                run_id,
                "completed",
                phase="run",
                status="failed",
            )

    async def _load_workflow_plan(self, flow_id: str) -> List[Task]:
        if self.workflow_repository is None:
            return []

        try:
            definition = await self.workflow_repository.get_definition(flow_id)
        except Exception:
            # A missing database must not prevent the local execution fallback.
            return []
        if not definition:
            return []
        return self._normalize_tasks(definition.get("tasks") or [])

    @staticmethod
    def _normalize_task(value: Task | Dict[str, Any]) -> Task:
        return value if isinstance(value, Task) else Task.model_validate(value)

    @classmethod
    def _normalize_tasks(cls, values: Sequence[Task | Dict[str, Any]]) -> List[Task]:
        return [cls._normalize_task(value) for value in values]

    @classmethod
    def _merge_plan(
        cls,
        current_plan: Sequence[Task],
        incoming_plan: Sequence[Task | Dict[str, Any]],
    ) -> List[Task]:
        merged = {task.id: task for task in current_plan}
        for value in incoming_plan:
            task = cls._normalize_task(value)
            merged[task.id] = task
        return sorted(merged.values(), key=lambda task: task.id)

    @staticmethod
    def _task_data(task: Task) -> Dict[str, Any]:
        return task.model_dump(mode="json")

    @staticmethod
    def _sse_event(
        run_id: str,
        event_type: str,
        *,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        task_id: Optional[str] = None,
        label: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
        **compatibility_fields: Any,
    ) -> str:
        """Encode a stable event envelope while retaining the existing UI fields."""

        event = ExecutionEvent(
            run_id=run_id,
            type=event_type,
            task_id=task_id,
            phase=phase,
            status=status,
            label=label,
            payload=payload or {},
        )
        data = event.model_dump(mode="json")
        if message is not None:
            data["message"] = message
        data.update(compatibility_fields)
        return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
