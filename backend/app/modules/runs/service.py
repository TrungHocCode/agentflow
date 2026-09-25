"""Application service for workflow run lifecycle and execution progress."""

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from app.core.config import settings
from app.execution.ports import ExecutionPort
from app.execution.state import State, Task
from app.modules.runs.events import (
    DiscardingRunEventPublisher,
    RunEventPublisher,
)
from app.modules.runs.models import RunDocument
from app.modules.runs.ports import RunRepository
from app.modules.runs.queue import (
    DiscardingRunCommandQueue,
    RunCommandQueue,
)
from app.modules.results.models import EvidenceRecord, ResultRecord
from app.modules.results.ports import ResearchDataRepository
from app.modules.workflows.ports import WorkflowRepository
from app.shared.commands import RunCommand
from app.shared.errors import ValidationError
from app.shared.events import ExecutionEvent
from app.shared.execution_metrics import (
    merge_execution_timings,
    serialize_execution_timings,
    summarize_execution_timings,
)


TERMINAL_RUN_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "abandoned",
}

LEGACY_EVENT_TYPES = {
    "run_started": "start",
    "run_progress": "log",
    "task_ready": "plan_update",
    "task_started": "task_update",
    "task_completed": "results_update",
    "task_failed": "error",
    "run_completed": "completed",
    "run_failed": "error",
    "run_interrupted": "error",
}


class RunService:
    """Coordinates run use cases through persistence, queue and execution ports."""

    def __init__(
        self,
        run_repository: RunRepository,
        workflow_repository: WorkflowRepository | None,
        execution_port: ExecutionPort,
        command_queue: RunCommandQueue | None = None,
        event_publisher: RunEventPublisher | None = None,
        research_repository: ResearchDataRepository | None = None,
        artifact_storage: Any | None = None,
    ) -> None:
        self.run_repository = run_repository
        self.workflow_repository = workflow_repository
        self.execution_port = execution_port
        self.command_queue = command_queue or DiscardingRunCommandQueue()
        self.event_publisher = event_publisher or DiscardingRunEventPublisher()
        self.research_repository = research_repository
        self.artifact_storage = artifact_storage

    async def save_run_doc(self, document: RunDocument) -> None:
        await self.run_repository.save(document)

    async def get_run(self, run_id: str, user_id: str | None = None) -> RunDocument | None:
        if user_id is None:
            return await self.run_repository.get(run_id)
        try:
            return await self.run_repository.get(run_id, user_id)
        except TypeError:
            # Compatibility for small in-memory adapters that predate ownership.
            document = await self.run_repository.get(run_id)
            return document if document and document.user_id == user_id else None

    async def list_runs(
        self,
        flow_id: Optional[str] = None,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[RunDocument]:
        try:
            documents = await self.run_repository.list(
                flow_id=flow_id,
                limit=limit,
                user_id=user_id,
            )
        except TypeError:
            documents = await self.run_repository.list(flow_id=flow_id, limit=limit)
        if user_id is not None:
            return [document for document in documents if document.user_id == user_id]
        return documents

    async def create_run(
        self,
        flow_id: str,
        input_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: str = "default_user",
    ) -> RunDocument:
        """Legacy build-phase entry point retained for compatibility."""

        run_id = str(uuid.uuid4())
        plan = await self._load_workflow_plan(flow_id)
        logs = [f"[RunService] Initialized Run {run_id} for Flow {flow_id}."]
        if input_message:
            logs.append(f"[User Input]: {input_message}")

        mode = "conversation"
        result_storage: List[Dict[str, Any]] = []
        execution_timings: List[Any] = []
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
                execution_timings = result_state.get("execution_timings") or []
            except Exception as exc:
                logs.append(f"[RunService Warning] Plan creation error: {exc}")

        metadata_values = dict(metadata or {})
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS and execution_timings:
            merged_timings = merge_execution_timings(
                metadata_values.get("execution_timings"),
                execution_timings,
            )
            metadata_values["execution_timings"] = merged_timings
            metadata_values["execution_metrics"] = summarize_execution_timings(merged_timings)

        document = RunDocument(
            run_id=run_id,
            flow_id=flow_id,
            user_id=user_id,
            workflow_version_id=self._version_id(flow_id, plan),
            status="pending",
            approval_status="pending",
            mode=mode,
            plan=plan,
            logs=logs,
            result_storage=result_storage,
            metadata=metadata_values,
        )
        await self.save_run_doc(document)
        await self._record_event(
            run_id,
            "run_progress",
            phase="build",
            status=document.status,
            payload={"flow_id": flow_id, "plan_size": len(plan)},
        )
        return document

    async def create_workflow_run(
        self,
        workflow_id: str,
        input_data: Optional[Dict[str, Any]] = None,
        execution_mode: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        user_id: str = "default_user",
        conversation_id: str | None = None,
    ) -> RunDocument | None:
        """Create and enqueue an asynchronous run from a workflow snapshot."""

        if self.workflow_repository is None:
            return None
        if idempotency_key:
            existing = await self.run_repository.find_by_idempotency_key(idempotency_key)
            if existing:
                return existing

        definition = await self._load_workflow_definition(workflow_id, user_id)
        if definition is None:
            return None
        plan = self._normalize_tasks(definition.get("tasks") or [])
        if not plan:
            raise ValidationError("Workflow must contain at least one task.")

        run_id = str(uuid.uuid4())
        version_id = await self._load_workflow_version_id(
            workflow_id,
            user_id,
            definition,
        )
        document_metadata = {
            **(metadata or {}),
            "workflow_snapshot": definition,
        }
        document = RunDocument(
            run_id=run_id,
            flow_id=workflow_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_version_id=version_id,
            status="queued",
            approval_status="not_required",
            execution_mode=execution_mode,
            mode="executing",
            plan=plan,
            metadata=document_metadata,
            input_data=input_data or {},
            idempotency_key=idempotency_key,
        )
        await self.save_run_doc(document)
        await self._record_event(
            run_id,
            "run_progress",
            phase="execute",
            status="queued",
            payload={"workflow_id": workflow_id, "workflow_version_id": version_id},
        )
        return await self._enqueue_document(document)

    async def send_message(
        self,
        run_id: str,
        message: str,
    ) -> Optional[RunDocument]:
        """Resume the build-phase conversation for a legacy run."""

        run_doc = await self.get_run(run_id)
        if not run_doc:
            return None
        if run_doc.status in TERMINAL_RUN_STATUSES:
            return run_doc

        try:
            result_state = await self.execution_port.continue_conversation(run_id, message)
            run_doc.plan = self._normalize_tasks(
                result_state.get("plan") or run_doc.plan
            )
            run_doc.mode = result_state.get("mode", run_doc.mode)
            run_doc.logs.extend(result_state.get("logs") or [])
            incoming_timings = result_state.get("execution_timings") or []
            if settings.ENABLE_EXECUTION_BENCHMARK_METRICS and incoming_timings:
                merged_timings = merge_execution_timings(
                    run_doc.metadata.get("execution_timings"),
                    incoming_timings,
                )
                run_doc.metadata["execution_timings"] = merged_timings
                run_doc.metadata["execution_metrics"] = summarize_execution_timings(merged_timings)
            run_doc.logs.append(f"[User Message]: {message}")
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_progress",
                phase="build",
                status=run_doc.status,
                payload={"message": message},
            )
        except Exception as exc:
            run_doc.logs.append(f"[RunService Warning] send_message error: {exc}")
            await self.save_run_doc(run_doc)
        return run_doc

    async def approve_run(
        self,
        run_id: str,
        approved: bool = True,
        feedback: Optional[str] = None,
        user_id: str | None = None,
    ) -> Optional[RunDocument]:
        """Approve a plan and enqueue it without blocking on execution."""

        run_doc = await self.get_run(run_id, user_id=user_id)
        if not run_doc:
            return None
        if run_doc.status in TERMINAL_RUN_STATUSES or run_doc.status in {"queued", "running"}:
            return run_doc

        if not approved:
            run_doc.logs.append(
                f"[User Approval]: Plan rejected. Feedback: {feedback or 'None'}"
            )
            run_doc.status = "failed"
            run_doc.approval_status = "rejected"
            run_doc.error_code = "workflow_rejected"
            run_doc.error_message = feedback or "Workflow plan rejected by user."
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_failed",
                phase="build",
                status=run_doc.status,
                payload={"reason": run_doc.error_message},
            )
            return run_doc

        run_doc.logs.append(
            f"[User Approval]: Plan approved. Feedback: {feedback or 'None'}"
        )
        run_doc.status = "queued"
        run_doc.approval_status = "approved"
        run_doc.updated_at = datetime.utcnow()
        await self.save_run_doc(run_doc)
        await self._record_event(
            run_id,
            "run_progress",
            phase="build",
            status="queued",
            payload={"feedback": feedback},
        )
        return await self._enqueue_document(run_doc)

    async def cancel_run(
        self,
        run_id: str,
        user_id: str | None = None,
    ) -> Optional[RunDocument]:
        """Cancel a run before or during execution."""

        run_doc = await self.get_run(run_id, user_id=user_id)
        if not run_doc:
            return None
        if run_doc.status in TERMINAL_RUN_STATUSES:
            return run_doc

        run_doc.status = "cancelled"
        run_doc.error_code = "cancelled_by_user"
        run_doc.error_message = "Run cancelled by user."
        run_doc.updated_at = datetime.utcnow()
        await self.save_run_doc(run_doc)
        await self._record_event(
            run_id,
            "run_interrupted",
            phase="execute",
            status=run_doc.status,
            payload={"reason": run_doc.error_message},
        )
        return run_doc

    async def retry_run(
        self,
        run_id: str,
        user_id: str | None = None,
    ) -> Optional[RunDocument]:
        """Create a new attempt from a failed/interrupted run snapshot."""

        source = await self.get_run(run_id, user_id=user_id)
        if source is None or source.status not in {"failed", "interrupted", "cancelled"}:
            return None
        retry_plan = [
            task.model_copy(update={"status": "pending", "error": None})
            if task.status in {"failed", "skipped"}
            else task
            for task in source.plan
        ]
        retry_count = int(source.metadata.get("retry_count", 0)) + 1
        retry = RunDocument(
            run_id=str(uuid.uuid4()),
            flow_id=source.flow_id,
            user_id=source.user_id,
            conversation_id=source.conversation_id,
            workflow_version_id=source.workflow_version_id,
            status="queued",
            approval_status="not_required",
            execution_mode=source.execution_mode,
            mode="executing",
            plan=retry_plan,
            result_storage=list(source.result_storage),
            metadata={
                **source.metadata,
                "retry_of": source.run_id,
                "retry_count": retry_count,
            },
            input_data=dict(source.input_data),
            resolved_model_config=dict(source.resolved_model_config),
        )
        await self.save_run_doc(retry)
        await self._record_event(
            retry.run_id,
            "run_progress",
            phase="execute",
            status="queued",
            payload={"retry_of": source.run_id, "retry_count": retry_count},
        )
        return await self._enqueue_document(retry)

    async def execute_queued_run(self, run_id: str) -> Optional[RunDocument]:
        """Execute one queued run; called by the background worker only."""

        run_doc = await self.run_repository.claim(run_id)
        if not run_doc:
            return await self.get_run(run_id)

        await self._record_event(
            run_id,
            "run_started",
            phase="execute",
            status="running",
            payload={"workflow_version_id": run_doc.workflow_version_id},
        )
        initial_state: State = {
            "messages": [],
            "plan": run_doc.plan,
            "current_task": run_doc.current_task,
            "logs": [],
            "result_storage": run_doc.result_storage,
            "mode": "executing",
            "metadata": {
                **run_doc.metadata,
                "input_data": run_doc.input_data,
                "resolved_model_config": run_doc.resolved_model_config,
            },
        }
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            initial_state["execution_timings"] = []
        execution_started_at = time.perf_counter()

        try:
            async for chunk in self.execution_port.execute_run(run_id, initial_state):
                latest = await self.get_run(run_id)
                if latest and latest.status == "cancelled":
                    return latest
                if not isinstance(chunk, dict):
                    continue
                for node_name, node_output in chunk.items():
                    if not isinstance(node_output, dict):
                        continue
                    events = self._apply_execution_output(
                        run_doc,
                        node_name,
                        node_output,
                    )
                    await self._persist_research_output(
                        run_doc,
                        node_name,
                        node_output,
                    )
                    run_doc.updated_at = datetime.utcnow()
                    await self.save_run_doc(run_doc)
                    for event in events:
                        await self._record_event_object(event)

            all_finished = bool(run_doc.plan) and all(
                task.status in ("done", "partial", "failed", "skipped")
                for task in run_doc.plan
            )
            has_failed_task = any(task.status == "failed" for task in run_doc.plan)
            partial_task_ids = [task.id for task in run_doc.plan if task.status == "partial"]
            if has_failed_task:
                run_doc.status = "failed"
                run_doc.error_code = "task_execution_failed"
                run_doc.error_message = "One or more workflow tasks failed."
            else:
                run_doc.status = "completed" if all_finished else "interrupted"
                if partial_task_ids:
                    run_doc.metadata["partial_task_ids"] = partial_task_ids
                    run_doc.metadata["has_partial_results"] = True
                    existing_warnings = list(run_doc.metadata.get("execution_warnings") or [])
                    for task in run_doc.plan:
                        if task.status != "partial":
                            continue
                        warning = task.error or f"Task {task.id} completed with incomplete source coverage."
                        if warning not in existing_warnings:
                            existing_warnings.append(warning)
                    run_doc.metadata["execution_warnings"] = existing_warnings
                    if run_doc.status == "completed":
                        run_doc.metadata["partial_completion"] = True
            if run_doc.status == "interrupted":
                run_doc.error_code = "execution_incomplete"
                run_doc.error_message = "Execution ended before all tasks completed."
            run_doc.execution_time_ms = (
                time.perf_counter() - execution_started_at
            ) * 1000
            self._finalize_execution_metrics(run_doc)
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            terminal_event_type = "run_failed" if run_doc.status == "failed" else "run_completed"
            await self._record_event(
                run_id,
                terminal_event_type,
                phase="execute",
                status=run_doc.status,
                payload={
                    "plan": [self._task_data(task) for task in run_doc.plan],
                    "results": run_doc.result_storage,
                    "message": run_doc.error_message,
                },
            )
        except Exception as exc:
            run_doc.status = "failed"
            run_doc.error_code = "execution_error"
            run_doc.error_message = str(exc)
            run_doc.logs.append(f"[RunService Error]: {exc}")
            run_doc.execution_time_ms = (
                time.perf_counter() - execution_started_at
            ) * 1000
            self._finalize_execution_metrics(run_doc)
            run_doc.updated_at = datetime.utcnow()
            await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_failed",
                phase="execute",
                status="failed",
                payload={"message": str(exc)},
            )
        return run_doc

    async def list_run_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> List[ExecutionEvent]:
        list_events = getattr(self.run_repository, "list_events", None)
        if list_events is None:
            return []
        return await self.run_repository.list_events(
            run_id=run_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def list_run_results(self, run_id: str, limit: int = 200):
        if self.research_repository is None:
            return []
        return await self.research_repository.list_results(run_id, limit)

    async def list_run_evidence(self, run_id: str, limit: int = 200):
        if self.research_repository is None:
            return []
        return await self.research_repository.list_evidence(run_id, limit)

    async def get_run_evidence(self, run_id: str, evidence_id: str):
        if self.research_repository is None:
            return None
        return await self.research_repository.get_evidence(evidence_id, run_id)

    async def list_run_artifacts(self, run_id: str, limit: int = 200):
        if self.research_repository is None:
            return []
        return await self.research_repository.list_artifacts(run_id, limit)

    async def get_run_artifact(self, run_id: str, artifact_id: str):
        if self.research_repository is None:
            return None
        return await self.research_repository.get_artifact(artifact_id, run_id)

    async def stream_run_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Observe persisted and live events; never starts execution."""

        run_doc = await self.get_run(run_id)
        if not run_doc:
            yield self._sse_event(
                run_id,
                "error",
                phase="run",
                status="failed",
                payload={"message": f"Run {run_id} not found"},
                message=f"Run {run_id} not found",
            )
            return

        seen_ids: set[str] = set()
        if after_event_id is None:
            yield self._sse_event(
                run_id,
                "start",
                phase="run",
                status=run_doc.status,
                payload={},
            )

        persisted_events = await self.list_run_events(
            run_id,
            after_event_id=after_event_id,
        )
        for event in persisted_events:
            seen_ids.add(event.event_id)
            yield self._event_frame(event)

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
                payload={},
            )
            return

        if run_doc.status in TERMINAL_RUN_STATUSES:
            if not any(event.type == "run_completed" for event in persisted_events):
                yield self._sse_event(
                    run_id,
                    "completed",
                    phase="run",
                    status=run_doc.status,
                    payload={},
                )
            return

        latest = await self.get_run(run_id)
        if latest and latest.status in TERMINAL_RUN_STATUSES:
            trailing = await self.list_run_events(run_id)
            for event in trailing:
                if event.event_id not in seen_ids:
                    seen_ids.add(event.event_id)
                    yield self._event_frame(event)
            return

        subscription = self.event_publisher.subscribe(run_id)
        iterator = subscription.__aiter__()
        pending_event: asyncio.Task[ExecutionEvent] | None = asyncio.create_task(
            iterator.__anext__()
        )
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(pending_event),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    latest = await self.get_run(run_id)
                    trailing = await self.list_run_events(run_id)
                    for trailing_event in trailing:
                        if trailing_event.event_id not in seen_ids:
                            seen_ids.add(trailing_event.event_id)
                            yield self._event_frame(trailing_event)
                    if latest and latest.status in TERMINAL_RUN_STATUSES:
                        return
                    continue
                except StopAsyncIteration:
                    return

                pending_event = None
                if event.event_id in seen_ids:
                    pending_event = asyncio.create_task(iterator.__anext__())
                    continue
                seen_ids.add(event.event_id)
                yield self._event_frame(event)
                if event.type in {"run_completed", "run_failed", "run_interrupted"}:
                    return
                pending_event = asyncio.create_task(iterator.__anext__())
        finally:
            if pending_event is not None and not pending_event.done():
                pending_event.cancel()
                try:
                    await pending_event
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            close_subscription = getattr(iterator, "aclose", None)
            if close_subscription is not None:
                await close_subscription()

    def _apply_execution_output(
        self,
        run_doc: RunDocument,
        node_name: str,
        node_output: Dict[str, Any],
    ) -> List[ExecutionEvent]:
        events: List[ExecutionEvent] = []
        logs = node_output.get("logs") or []
        for log in logs:
            run_doc.logs.append(log)
            events.append(
                ExecutionEvent(
                    run_id=run_doc.run_id,
                    type="run_progress",
                    phase="execute",
                    status="running",
                    label=node_name,
                    payload={"message": str(log), "node": node_name},
                )
            )

        incoming_timings = node_output.get("execution_timings") or []
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS and incoming_timings:
            merged_timings = merge_execution_timings(
                run_doc.metadata.get("execution_timings"),
                incoming_timings,
            )
            run_doc.metadata["execution_timings"] = merged_timings
            run_doc.metadata["execution_metrics"] = summarize_execution_timings(merged_timings)

        current_task = node_output.get("current_task")
        if current_task:
            normalized_task = self._normalize_task(current_task)
            run_doc.current_task = normalized_task
            task_data = self._task_data(normalized_task)
            events.append(
                ExecutionEvent(
                    run_id=run_doc.run_id,
                    type="task_started",
                    task_id=str(normalized_task.id),
                    phase="execute",
                    status="running",
                    label=node_name,
                    payload={"task": task_data},
                )
            )

        plan = node_output.get("plan")
        if plan:
            run_doc.plan = self._merge_plan(run_doc.plan, plan)
            plan_data = [self._task_data(task) for task in run_doc.plan]
            events.append(
                ExecutionEvent(
                    run_id=run_doc.run_id,
                    type="task_ready",
                    phase="execute",
                    status="running",
                    label=node_name,
                    payload={"plan": plan_data},
                )
            )

        result_storage = node_output.get("result_storage")
        if result_storage:
            values = result_storage if isinstance(result_storage, list) else [result_storage]
            run_doc.result_storage.extend(values)
            events.append(
                ExecutionEvent(
                    run_id=run_doc.run_id,
                    type="task_completed",
                    phase="execute",
                    status="running",
                    label=node_name,
                    payload={"results": run_doc.result_storage},
                )
            )
        return events

    @staticmethod
    def _finalize_execution_metrics(run_doc: RunDocument) -> None:
        if not settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            return
        metrics = run_doc.metadata.get("execution_metrics")
        if not metrics:
            return
        execute_phase = metrics.get("phase_totals_ms", {}).get("execute", {})
        execute_call_time_ms = float(execute_phase.get("total_ms", 0.0))
        metrics["execute_wall_ms"] = round(run_doc.execution_time_ms, 3)
        metrics["unattributed_execute_ms"] = round(
            max(0.0, run_doc.execution_time_ms - execute_call_time_ms),
            3,
        )

    async def _persist_research_output(
        self,
        run_doc: RunDocument,
        node_name: str,
        node_output: Dict[str, Any],
    ) -> None:
        """Project execution output into queryable results, evidence and artifacts."""

        if self.research_repository is None:
            return
        values = node_output.get("result_storage")
        if not values:
            return
        items = values if isinstance(values, list) else [values]
        ingested_paths: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                item = {"result": item}
            content = item.get("result", item.get("content", item))
            if hasattr(content, "model_dump"):
                content = content.model_dump(mode="json")
            task_id = item.get("task_id") or item.get("task") or item.get("id")
            result = ResultRecord(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"agentflow:result:{run_doc.run_id}:{task_id}:{json.dumps(content, sort_keys=True, default=str)}",
                    )
                ),
                run_id=run_doc.run_id,
                task_id=str(task_id) if task_id is not None else None,
                result_type=self._infer_result_type(node_name, item),
                content=content,
                metadata={"node": node_name, "status": item.get("status", "done")},
            )
            await self.research_repository.save_result(result)

            serialized = json.dumps(content, ensure_ascii=False, default=str)
            for source_url in sorted(set(re.findall(r"https?://[^\s<>\"']+", serialized))):
                evidence_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"agentflow:evidence:{run_doc.run_id}:{task_id}:{source_url}",
                    )
                )
                await self.research_repository.save_evidence(
                    EvidenceRecord(
                        id=evidence_id,
                        run_id=run_doc.run_id,
                        task_execution_id=str(task_id) if task_id is not None else None,
                        source_url=source_url.rstrip(".,);"),
                        source_type=self._infer_source_type(source_url),
                        excerpt=serialized[:500],
                        metadata={"node": node_name},
                    )
                )

            if self.artifact_storage is not None:
                artifact_paths = set(self._find_artifact_paths(serialized))
                raw_logs = node_output.get("logs") or []
                logs = raw_logs if isinstance(raw_logs, list) else [raw_logs]
                for log in logs:
                    log_text = str(log)
                    if any(
                        marker in log_text
                        for marker in (
                            "Tool 'markdown_report_generator' result:",
                            "Tool 'chart_generator' result:",
                        )
                    ):
                        tool_result = log_text.split(" result:", 1)[-1].strip()
                        artifact_paths.update(self._find_artifact_paths(tool_result))

                for source_path in artifact_paths:
                    if source_path in ingested_paths:
                        continue
                    if not self._is_managed_artifact_path(source_path):
                        continue
                    ingested_paths.add(source_path)
                    artifact = self.artifact_storage.ingest_file(
                        source_path=source_path,
                        user_id=run_doc.user_id,
                        run_id=run_doc.run_id,
                        task_execution_id=str(task_id) if task_id is not None else None,
                    )
                    if artifact is not None:
                        await self.research_repository.save_artifact(artifact)

    @staticmethod
    def _infer_result_type(node_name: str, item: Dict[str, Any]) -> str:
        explicit = item.get("result_type")
        if explicit in {"raw_data", "normalized_data", "summary", "comparison", "chart_spec", "report"}:
            return explicit
        lowered = node_name.lower()
        if "chart" in lowered:
            return "chart_spec"
        if "report" in lowered:
            return "report"
        if "summar" in lowered:
            return "summary"
        return "raw_data"

    @staticmethod
    def _infer_source_type(source_url: str) -> str:
        lowered = source_url.lower()
        if "github.com" in lowered or "gitlab.com" in lowered:
            return "repository"
        if "/docs" in lowered or "readthedocs" in lowered:
            return "documentation"
        if "/api" in lowered:
            return "api"
        return "article"

    @staticmethod
    def _find_artifact_paths(serialized: str) -> List[str]:
        paths: list[str] = []
        try:
            payload = json.loads(serialized)
        except (json.JSONDecodeError, TypeError):
            payload = None

        if isinstance(payload, dict) and payload.get("ok") is True:
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("file_path", "svg_path", "spec_path"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        paths.append(value.strip())

        for match in re.findall(r"(?:File Path|Chart Spec Path):\s*([^,\n]+)", serialized):
            path = match.strip().strip("`\"'")
            if path:
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _is_managed_artifact_path(source_path: str) -> bool:
        """Only ingest generated files from AgentFlow's report/chart workspace."""

        try:
            candidate = Path(source_path).resolve()
            workspace = Path(__file__).resolve().parents[4] / "workspace_data"
            allowed_roots = (workspace / "reports", workspace / "charts")
            return candidate.is_file() and any(
                candidate == root or root in candidate.parents
                for root in allowed_roots
            )
        except (OSError, RuntimeError):
            return False

    async def _enqueue_document(self, document: RunDocument) -> RunDocument:
        command = RunCommand(
            command_id=str(uuid.uuid4()),
            run_id=document.run_id,
            workflow_id=document.flow_id,
            workflow_version_id=document.workflow_version_id,
            requested_by=document.user_id,
            metadata={"input_data": document.input_data},
        )
        try:
            await self.command_queue.enqueue(command)
            await self._record_event(
                document.run_id,
                "run_progress",
                phase="execute",
                status="queued",
                payload={"command_id": command.command_id},
            )
        except Exception as exc:
            document.status = "interrupted"
            document.error_code = "queue_unavailable"
            document.error_message = "Execution queue is unavailable."
            document.logs.append(f"[RunService Queue Error]: {exc}")
            document.updated_at = datetime.utcnow()
            await self.save_run_doc(document)
            await self._record_event(
                document.run_id,
                "run_failed",
                phase="execute",
                status=document.status,
                payload={"message": document.error_message},
            )
        return document

    async def _load_workflow_definition(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> Dict[str, Any] | None:
        if self.workflow_repository is None:
            return None
        return await self.workflow_repository.get_definition(workflow_id, user_id)

    async def _load_workflow_plan(self, flow_id: str) -> List[Task]:
        definition = await self._load_workflow_definition(flow_id)
        return self._normalize_tasks(definition.get("tasks") or []) if definition else []

    async def _load_workflow_version_id(
        self,
        workflow_id: str,
        user_id: str,
        definition: Dict[str, Any],
    ) -> str:
        if self.workflow_repository is not None:
            get_current_version = getattr(
                self.workflow_repository,
                "get_current_version",
                None,
            )
            if get_current_version is not None:
                version = await get_current_version(workflow_id, user_id)
                if version is not None and version.version_id:
                    return version.version_id
        return self._version_id(workflow_id, definition)

    async def _record_event(
        self,
        run_id: str,
        event_type: str,
        *,
        phase: str,
        status: str,
        payload: Dict[str, Any],
        task_id: str | None = None,
        label: str | None = None,
    ) -> ExecutionEvent:
        return await self._record_event_object(
            ExecutionEvent(
                run_id=run_id,
                type=event_type,
                task_id=task_id,
                phase=phase,
                status=status,
                label=label,
                payload=payload,
            )
        )

    async def _record_event_object(self, event: ExecutionEvent) -> ExecutionEvent:
        append_event = getattr(self.run_repository, "append_event", None)
        persisted = await append_event(event) if append_event else event
        try:
            await self.event_publisher.publish(persisted)
        except Exception:
            # Durable event replay remains available when live fan-out is down.
            pass
        return persisted

    @staticmethod
    def _event_frame(event: ExecutionEvent) -> str:
        data = event.model_dump(mode="json")
        data["legacy_type"] = LEGACY_EVENT_TYPES.get(event.type, event.type)
        for key in ("message", "task", "plan", "results"):
            if key in event.payload:
                data[key] = event.payload[key]
        return (
            f"id: {event.event_id}\n"
            f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        )

    @staticmethod
    def _sse_event(
        run_id: str,
        event_type: str,
        *,
        phase: str,
        status: str,
        payload: Dict[str, Any],
        message: str | None = None,
        **compatibility_fields: Any,
    ) -> str:
        event = ExecutionEvent(
            run_id=run_id,
            type=event_type,
            phase=phase,
            status=status,
            payload=payload,
        )
        data = event.model_dump(mode="json")
        data["legacy_type"] = LEGACY_EVENT_TYPES.get(event_type, event_type)
        if message is not None:
            data["message"] = message
        data.update(compatibility_fields)
        return (
            f"id: {event.event_id}\n"
            f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        )

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
    def _version_id(workflow_id: str, definition: Any) -> str:
        serialized = json.dumps(
            definition,
            sort_keys=True,
            default=lambda value: value.model_dump() if hasattr(value, "model_dump") else str(value),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentflow:{workflow_id}:{digest}"))
