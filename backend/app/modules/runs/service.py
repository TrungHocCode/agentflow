"""Application service for workflow run lifecycle and execution progress."""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from app.core.config import settings
from app.execution.ports import ExecutionPort
from app.execution.model_router import InferencePurpose
from app.execution.state import State, Task
from app.modules.runs.events import (
    DiscardingRunEventPublisher,
    RunEventPublisher,
)
from app.modules.runs.models import RunDocument
from app.modules.runs.ports import RunRepository
from app.modules.conversations.ports import ConversationRepository
from app.modules.catalog.ports import CatalogRepository
from app.modules.runs.queue import (
    DiscardingRunCommandQueue,
    RunCommandQueue,
)
from app.modules.results.models import EvidenceRecord, ResultRecord
from app.modules.results.ports import ArtifactStorage, ResearchDataRepository
from app.modules.workflows.ports import WorkflowRepository
from app.shared.commands import RunCommand
from app.shared.errors import ConflictError, PersistenceError, ValidationError
from app.shared.events import ExecutionEvent
from app.shared.execution_metrics import (
    merge_execution_timings,
    serialize_execution_timings,
    summarize_execution_timings,
)
from app.shared.llm_call_metrics import merge_llm_call_metrics, summarize_llm_call_metrics
from app.shared.task_metrics import (
    merge_task_execution_metrics,
    summarize_task_execution_metrics,
)
from app.shared.observability import bind_context, current_context


logger = logging.getLogger(__name__)


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
        artifact_storage: ArtifactStorage | None = None,
        conversation_repository: ConversationRepository | None = None,
        catalog_repository: CatalogRepository | None = None,
    ) -> None:
        self.run_repository = run_repository
        self.workflow_repository = workflow_repository
        self.execution_port = execution_port
        self.command_queue = command_queue or DiscardingRunCommandQueue()
        self.event_publisher = event_publisher or DiscardingRunEventPublisher()
        self.research_repository = research_repository
        self.artifact_storage = artifact_storage
        self.conversation_repository = conversation_repository
        self.catalog_repository = catalog_repository

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

        run_metadata = dict(metadata or {})
        run_metadata.pop("model_name", None)
        run_metadata["inference_purpose"] = InferencePurpose.PLANNER.value
        run_metadata["use_llm"] = True
        run_id = str(uuid.uuid4())
        plan = await self._load_workflow_plan(flow_id)
        logs = [f"[RunService] Initialized Run {run_id} for Flow {flow_id}."]
        if input_message:
            logs.append("[User Input] Request accepted for planning.")

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
                    "metadata": run_metadata,
                }
                result_state = await self.execution_port.create_plan(run_id, initial_state)
                plan = self._normalize_tasks(result_state.get("plan") or [])
                mode = result_state.get("mode", "conversation")
                logs = result_state.get("logs") or logs
                result_storage = result_state.get("result_storage") or []
                execution_timings = result_state.get("execution_timings") or []
            except Exception as exc:
                error_id = str(uuid.uuid4())
                logger.error(
                    "Legacy run planning failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"error_id": error_id, "error_code": "planning_failed"},
                )
                metadata_values = run_metadata
                metadata_values["last_error"] = {
                    "error_id": error_id,
                    "code": "planning_failed",
                    "category": "model",
                    "retryable": True,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
                logs.append(f"[RunService Warning] Plan creation failed (reference {error_id}).")

        if plan:
            plan = await self._validate_task_plan(plan)

        metadata_values = dict(run_metadata)
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
            # This endpoint only creates a planning draft. It has not selected
            # an immutable, published workflow version and must not invent one.
            workflow_version_id=None,
            plan_revision=self._plan_revision(plan),
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
        workflow_version_id: str,
        input_data: Optional[Dict[str, Any]] = None,
        execution_mode: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        user_id: str = "default_user",
        conversation_id: str | None = None,
    ) -> RunDocument | None:
        """Create and enqueue an asynchronous run from a workflow snapshot."""

        from app.modules.workflows.contract import normalize_workflow_definition
        from app.modules.workflows.validator import (
            validate_workflow_definition,
            validate_workflow_references,
        )
        from app.shared.errors import ResourceNotFoundError

        if self.workflow_repository is None:
            return None
        if not workflow_version_id:
            raise ValidationError("A published workflow version must be selected before creating a run.")
        if conversation_id is not None and self.conversation_repository is not None:
            conversation = await self.conversation_repository.get(conversation_id, user_id)
            if conversation is None:
                raise ResourceNotFoundError(
                    "The linked conversation was not found.",
                    entity="conversation",
                )
            if conversation.workflow_id and conversation.workflow_id != workflow_id:
                raise ConflictError(
                    "The linked conversation belongs to a different workflow.",
                    code="conversation_workflow_mismatch",
                )
        version_snapshot = None
        if self.workflow_repository is not None and workflow_version_id:
            loader = getattr(
                self.workflow_repository,
                "get_published_version_snapshot",
                None,
            )
            if loader is not None:
                version_snapshot = await loader(workflow_id, workflow_version_id, user_id)
            else:
                version_snapshot = await self.workflow_repository.get_version(
                    workflow_id,
                    workflow_version_id,
                    user_id,
                )
                if version_snapshot and version_snapshot.status != "published":
                    version_snapshot = None
        if version_snapshot is None:
            return None

        definition = normalize_workflow_definition(version_snapshot.definition)
        validate_workflow_definition(definition, require_steps=True)
        definition = await validate_workflow_references(definition, self.catalog_repository)
        validate_workflow_definition(definition, require_steps=True)
        plan = self._normalize_tasks(definition.get("tasks") or [])
        if not plan:
            raise ValidationError("Workflow must contain at least one task.")
        workflow_version_id = str(
            getattr(version_snapshot, "version_id", None)
            or getattr(version_snapshot, "id", None)
            or workflow_version_id
        )
        resolved_model_config = await self._resolve_execution_snapshot(
            definition,
            plan,
            workflow_version_id=workflow_version_id,
        )
        fingerprint = self._idempotency_fingerprint(
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            input_data=input_data or {},
            execution_mode=execution_mode,
            metadata=metadata or {},
            conversation_id=conversation_id,
        )
        if idempotency_key:
            existing = await self.run_repository.find_by_idempotency_key(
                idempotency_key,
                user_id,
            )
            if existing is not None:
                if existing.idempotency_fingerprint == fingerprint:
                    return existing
                raise ConflictError(
                    "This idempotency key was already used for a different run request.",
                    code="idempotency_key_reused",
                )

        run_id = str(uuid.uuid4())
        plan = [
            task.model_copy(
                update={
                    "task_execution_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"agentflow:{run_id}:task:{task.task_key or task.id}",
                        )
                    )
                }
            )
            for task in plan
        ]
        document_metadata = dict(metadata or {})
        document_metadata.pop("model_name", None)
        document_metadata.pop("inference_purpose", None)
        document_metadata["use_llm"] = True
        document_metadata["workflow_snapshot"] = definition
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            document_metadata["request_started_at"] = datetime.now(timezone.utc).isoformat()
        document = RunDocument(
            run_id=run_id,
            flow_id=workflow_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_version_id=workflow_version_id,
            plan_revision=self._plan_revision(plan),
            approved_plan_revision=self._plan_revision(plan),
            status="queued",
            approval_status="approved",
            execution_mode=execution_mode,
            mode="executing",
            plan=plan,
            resolved_model_config=resolved_model_config,
            metadata=document_metadata,
            input_data=input_data or {},
            idempotency_key=idempotency_key,
            idempotency_fingerprint=fingerprint if idempotency_key else None,
        )
        try:
            await self.save_run_doc(document)
        except PersistenceError:
            # The database unique constraint is the concurrency arbiter. If a
            # simultaneous identical request won, return that run; otherwise
            # preserve the original persistence failure or report key reuse.
            if idempotency_key:
                try:
                    existing = await self.run_repository.find_by_idempotency_key(
                        idempotency_key,
                        user_id,
                    )
                except Exception:
                    existing = None
                if existing is not None:
                    if existing.idempotency_fingerprint == fingerprint:
                        return existing
                    raise ConflictError(
                        "This idempotency key was already used for a different run request.",
                        code="idempotency_key_reused",
                    )
            raise
        await self._record_event(
            run_id,
            "run_progress",
            phase="execute",
            status="queued",
            payload={
                "workflow_id": workflow_id,
                "workflow_version_id": workflow_version_id,
            },
        )
        return await self._enqueue_document(document)

    async def send_message(
        self,
        run_id: str,
        message: str,
        user_id: str | None = None,
    ) -> Optional[RunDocument]:
        """Resume the build-phase conversation for a legacy run."""

        run_doc = await self.get_run(run_id, user_id=user_id)
        if not run_doc:
            return None
        if run_doc.status in TERMINAL_RUN_STATUSES:
            return run_doc
        if run_doc.status not in {"pending", "created", "waiting_for_approval", "paused"}:
            raise ConflictError(
                "The plan can no longer be changed after approval or execution has started.",
                code="plan_is_locked",
            )
        expected_revision = run_doc.plan_revision or self._plan_revision(run_doc.plan)
        expected_updated_at = run_doc.updated_at

        try:
            run_doc.metadata["use_llm"] = True
            result_state = await self.execution_port.continue_conversation(run_id, message)
            run_doc.plan = self._normalize_tasks(
                result_state.get("plan") or run_doc.plan
            )
            run_doc.plan_revision = self._plan_revision(run_doc.plan)
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
            self._accumulate_llm_call_metrics(run_doc, result_state)
            run_doc.logs.append("[User Message] Follow-up accepted for planning.")
            run_doc.updated_at = datetime.now(timezone.utc)
            saver = getattr(self.run_repository, "save_if_plan_revision_matches", None)
            if saver is not None:
                saved = await saver(
                    run_doc,
                    expected_revision,
                    expected_updated_at,
                    ("pending", "created", "waiting_for_approval", "paused"),
                )
                if not saved:
                    raise ConflictError(
                        "The plan changed while this message was being processed. Please reload it.",
                        code="plan_revision_changed",
                    )
            else:
                await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_progress",
                phase="build",
                status=run_doc.status,
                payload={"message": message},
            )
        except ConflictError:
            raise
        except Exception as exc:
            error_id = str(uuid.uuid4())
            logger.error(
                "Legacy run conversation update failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"run_id": run_id, "error_id": error_id, "error_code": "conversation_update_failed"},
            )
            run_doc.metadata["last_error"] = {
                "error_id": error_id,
                "code": "conversation_update_failed",
                "category": "model",
                "retryable": True,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
            run_doc.logs.append(f"[RunService Warning] Conversation update failed (reference {error_id}).")
            try:
                await self.save_run_doc(run_doc)
            except Exception as persistence_exc:
                logger.error(
                    "Could not persist legacy conversation failure",
                    exc_info=(
                        type(persistence_exc),
                        persistence_exc,
                        persistence_exc.__traceback__,
                    ),
                    extra={"run_id": run_id, "error_id": error_id},
                )
        return run_doc

    async def approve_run(
        self,
        run_id: str,
        approved: bool = True,
        feedback: Optional[str] = None,
        user_id: str | None = None,
        plan_revision: str | None = None,
    ) -> Optional[RunDocument]:
        """Approve a plan and enqueue it without blocking on execution."""

        from app.shared.errors import ConflictError

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
            run_doc.updated_at = datetime.now(timezone.utc)
            await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_failed",
                phase="build",
                status=run_doc.status,
                payload={"reason": run_doc.error_message},
            )
            return run_doc

        if not run_doc.plan:
            raise ValidationError("A workflow plan must contain at least one task before approval.")
        if not run_doc.workflow_version_id or self.workflow_repository is None:
            raise ConflictError(
                "Save and publish this plan as a workflow version before approving it for execution.",
                code="workflow_version_required",
            )
        expected_updated_at = run_doc.updated_at
        validated_plan = await self._validate_task_plan(run_doc.plan)
        current_revision = self._plan_revision(validated_plan)
        persisted_revision = run_doc.plan_revision or self._plan_revision(run_doc.plan)
        if persisted_revision != current_revision:
            raise ConflictError(
                "The workflow plan is no longer identical to the reviewed revision.",
                code="plan_revision_changed",
            )
        snapshot_loader = getattr(
            self.workflow_repository,
            "get_published_version_snapshot",
            None,
        )
        if snapshot_loader is not None:
            version_snapshot = await snapshot_loader(
                run_doc.flow_id,
                run_doc.workflow_version_id,
                run_doc.user_id,
            )
        else:
            version_snapshot = await self.workflow_repository.get_version(
                run_doc.flow_id,
                run_doc.workflow_version_id,
                run_doc.user_id,
            )
            if version_snapshot is not None and version_snapshot.status != "published":
                version_snapshot = None
        if version_snapshot is None:
            raise ConflictError(
                "The selected published workflow version is no longer available.",
                code="workflow_version_unavailable",
            )
        from app.modules.workflows.contract import normalize_workflow_definition

        version_definition = normalize_workflow_definition(version_snapshot.definition)
        version_plan = await self._validate_task_plan(
            self._normalize_tasks(version_definition.get("tasks") or [])
        )
        if self._plan_revision(version_plan) != current_revision:
            raise ConflictError(
                "The reviewed plan does not match the selected published workflow version.",
                code="workflow_version_mismatch",
            )
        if not plan_revision:
            raise ValidationError("The plan revision is required to approve a workflow.")
        if plan_revision != current_revision:
            raise ConflictError(
                "The plan changed after it was reviewed. Review the latest plan before approving.",
                code="plan_revision_changed",
            )
        run_doc.plan = validated_plan

        run_doc.logs.append(
            f"[User Approval]: Plan approved. Feedback: {feedback or 'None'}"
        )
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            run_doc.metadata["request_started_at"] = datetime.now(timezone.utc).isoformat()
        run_doc.status = "queued"
        run_doc.approval_status = "approved"
        run_doc.plan_revision = current_revision
        run_doc.approved_plan_revision = current_revision
        run_doc.updated_at = datetime.now(timezone.utc)
        saver = getattr(self.run_repository, "save_if_plan_revision_matches", None)
        if saver is not None:
            saved = await saver(
                run_doc,
                current_revision,
                expected_updated_at,
                ("pending", "created", "waiting_for_approval", "paused"),
            )
            if not saved:
                raise ConflictError(
                    "The plan changed before approval could be recorded. Review the latest plan.",
                    code="plan_revision_changed",
                )
        else:
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
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            worker_started_at = self._parse_metric_timestamp(
                run_doc.metadata.get("worker_started_at")
            )
            if worker_started_at:
                run_doc.execution_time_ms = max(
                    0.0,
                    (datetime.now(timezone.utc) - worker_started_at).total_seconds() * 1000,
                )
            self._finalize_run_timing_metrics(run_doc)
            self._finalize_execution_metrics(run_doc)
        run_doc.updated_at = datetime.now(timezone.utc)
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
        if (
            self.workflow_repository is None
            or not source.workflow_version_id
            or not source.approved_plan_revision
            or source.approved_plan_revision != self._plan_revision(source.plan)
        ):
            raise ConflictError(
                "This run is not bound to an intact approved workflow version and cannot be retried.",
                code="workflow_version_required",
            )
        version_snapshot = await self.workflow_repository.get_version(
            source.flow_id,
            source.workflow_version_id,
            source.user_id,
        )
        if version_snapshot is None or version_snapshot.status != "published":
            raise ConflictError(
                "The original published workflow version is no longer available for retry.",
                code="workflow_version_unavailable",
            )
        from app.modules.workflows.contract import normalize_workflow_definition

        version_definition = normalize_workflow_definition(version_snapshot.definition)
        version_plan = await self._validate_task_plan(
            self._normalize_tasks(version_definition.get("tasks") or [])
        )
        if self._plan_revision(version_plan) != source.approved_plan_revision:
            raise ConflictError(
                "The failed run no longer matches its approved workflow version.",
                code="workflow_version_mismatch",
            )
        retry_plan = [
            task.model_copy(update={"status": "pending", "error": None})
            if task.status in {"failed", "skipped"}
            else task
            for task in source.plan
        ]
        retry_count = int(source.metadata.get("retry_count", 0)) + 1
        retry_metadata = dict(source.metadata)
        retry_metadata.pop("last_error", None)
        retry_metadata["use_llm"] = True
        retry_metadata["retry_of"] = source.run_id
        retry_metadata["retry_count"] = retry_count
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            retry_metadata["request_started_at"] = datetime.now(timezone.utc).isoformat()
        retry = RunDocument(
            run_id=str(uuid.uuid4()),
            flow_id=source.flow_id,
            user_id=source.user_id,
            conversation_id=source.conversation_id,
            workflow_version_id=source.workflow_version_id,
            plan_revision=self._plan_revision(retry_plan),
            approved_plan_revision=self._plan_revision(retry_plan),
            status="queued",
            approval_status="approved",
            execution_mode=source.execution_mode,
            mode="executing",
            plan=retry_plan,
            result_storage=list(source.result_storage),
            metadata=retry_metadata,
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
        with bind_context(run_id=run_id):
            return await self._execute_queued_run(run_id)

    async def _execute_queued_run(self, run_id: str) -> Optional[RunDocument]:
        """Execute one queued run; called by the background worker only."""

        run_doc = await self.run_repository.claim(run_id)
        if not run_doc:
            return await self.get_run(run_id)
        current_revision = self._plan_revision(run_doc.plan)
        if (
            self.workflow_repository is not None
            and (
                not run_doc.workflow_version_id
                or not run_doc.approved_plan_revision
                or run_doc.approved_plan_revision != current_revision
            )
        ):
            run_doc.status = "failed"
            run_doc.error_code = "approved_workflow_snapshot_invalid"
            run_doc.error_message = (
                "The run is not bound to an intact approved workflow version."
            )
            run_doc.updated_at = datetime.now(timezone.utc)
            await self.save_run_doc(run_doc)
            await self._record_event(
                run_id,
                "run_failed",
                phase="execute",
                status=run_doc.status,
                payload={"code": run_doc.error_code},
            )
            return run_doc
        logger.info(
            "Workflow run claimed by execution worker",
            extra={"run_id": run_id, "run_status": "running"},
        )

        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            worker_started_at = datetime.now(timezone.utc)
            run_doc.metadata["worker_started_at"] = worker_started_at.isoformat()
            queued_at = self._parse_metric_timestamp(run_doc.metadata.get("queued_at"))
            request_started_at = self._parse_metric_timestamp(
                run_doc.metadata.get("request_started_at")
            )
            if queued_at:
                run_doc.metadata["queue_wait_ms"] = round(
                    max(0.0, (worker_started_at - queued_at).total_seconds() * 1000),
                    3,
                )
            if request_started_at:
                run_doc.metadata["approval_or_request_wait_ms"] = round(
                    max(0.0, (worker_started_at - request_started_at).total_seconds() * 1000),
                    3,
                )

        try:
            await self._record_event(
                run_id,
                "run_started",
                phase="execute",
                status="running",
                payload={"workflow_version_id": run_doc.workflow_version_id},
            )
        except Exception as exc:
            logger.error(
                "Run start event could not be recorded; execution will continue",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"run_id": run_id, "error_code": "run_event_persist_failed"},
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
                    current_task = node_output.get("current_task")
                    task_id = (
                        str(current_task.id)
                        if hasattr(current_task, "id")
                        else str(current_task.get("id"))
                        if isinstance(current_task, dict) and current_task.get("id") is not None
                        else None
                    )
                    with bind_context(task_execution_id=task_id):
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
                        run_doc.updated_at = datetime.now(timezone.utc)
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
                previous_error = run_doc.metadata.get("last_error") or {}
                run_doc.error_code = previous_error.get("code", "task_execution_failed")
                failure_id = previous_error.get("error_id") or str(uuid.uuid4())
                run_doc.error_message = (
                    f"One or more workflow tasks failed. Reference: {failure_id}."
                )
                run_doc.metadata["last_error"] = {
                    **previous_error,
                    "error_id": failure_id,
                    "code": run_doc.error_code,
                    "category": previous_error.get("category", "execution"),
                    "retryable": previous_error.get("retryable", False),
                    "occurred_at": previous_error.get("occurred_at", datetime.now(timezone.utc).isoformat()),
                }
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
                previous_error = run_doc.metadata.get("last_error") or {}
                failure_id = previous_error.get("error_id") or str(uuid.uuid4())
                run_doc.error_message = (
                    f"Execution ended before all tasks completed. Reference: {failure_id}."
                )
                run_doc.metadata["last_error"] = {
                    **previous_error,
                    "error_id": failure_id,
                    "code": run_doc.error_code,
                    "category": previous_error.get("category", "execution"),
                    "retryable": previous_error.get("retryable", True),
                    "occurred_at": previous_error.get("occurred_at", datetime.now(timezone.utc).isoformat()),
                }
            run_doc.execution_time_ms = (
                time.perf_counter() - execution_started_at
            ) * 1000
            self._finalize_run_timing_metrics(run_doc)
            self._finalize_execution_metrics(run_doc)
            run_doc.updated_at = datetime.now(timezone.utc)
            await self.save_run_doc(run_doc)
            logger.info(
                "Workflow run reached terminal state",
                extra={
                    "run_id": run_id,
                    "run_status": run_doc.status,
                    "duration_ms": round(run_doc.execution_time_ms or 0, 3),
                },
            )
            terminal_event_type = {
                "failed": "run_failed",
                "interrupted": "run_interrupted",
            }.get(run_doc.status, "run_completed")
            terminal_error = run_doc.metadata.get("last_error") or {}
            try:
                await self._record_event(
                    run_id,
                    terminal_event_type,
                    phase="execute",
                    status=run_doc.status,
                    payload={
                        "plan": [self._task_data(task) for task in run_doc.plan],
                        "results": run_doc.result_storage,
                        "message": run_doc.error_message,
                        "error_id": terminal_error.get("error_id"),
                        "code": run_doc.error_code,
                    },
                )
            except Exception as event_exc:
                # The durable terminal run state has already been saved. A failure to
                # publish its final event must not rewrite a completed run as failed.
                logger.error(
                    "Terminal run event could not be recorded",
                    exc_info=(type(event_exc), event_exc, event_exc.__traceback__),
                    extra={
                        "run_id": run_id,
                        "run_status": run_doc.status,
                        "error_code": "terminal_run_event_persist_failed",
                    },
                )
        except Exception as exc:
            error_id = str(uuid.uuid4())
            error_code = exc.code if isinstance(exc, PersistenceError) else "execution_error"
            safe_message = (
                f"Run data could not be saved. Please retry after storage is available. Reference: {error_id}."
                if isinstance(exc, PersistenceError)
                else f"Workflow execution failed unexpectedly. Please retry the run. Reference: {error_id}."
            )
            logger.error(
                "Queued workflow execution failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "error_id": error_id,
                    "error_code": error_code,
                    "run_status": "failed",
                },
            )
            run_doc.status = "failed"
            run_doc.error_code = error_code
            run_doc.error_message = safe_message
            run_doc.metadata["last_error"] = {
                "error_id": error_id,
                "code": error_code,
                "category": "dependency" if isinstance(exc, PersistenceError) else "execution",
                "retryable": isinstance(exc, PersistenceError),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
            run_doc.logs.append(f"[RunService Error] Execution failed (reference {error_id}).")
            run_doc.execution_time_ms = (
                time.perf_counter() - execution_started_at
            ) * 1000
            self._finalize_run_timing_metrics(run_doc)
            self._finalize_execution_metrics(run_doc)
            run_doc.updated_at = datetime.now(timezone.utc)
            try:
                await self.save_run_doc(run_doc)
            except Exception as persistence_exc:
                logger.error(
                    "Could not persist terminal run failure",
                    exc_info=(
                        type(persistence_exc),
                        persistence_exc,
                        persistence_exc.__traceback__,
                    ),
                    extra={"error_id": error_id, "error_code": "run_failure_persistence_failed"},
                )
            try:
                await self._record_event(
                    run_id,
                    "run_failed",
                    phase="execute",
                    status="failed",
                    payload={"message": safe_message, "error_id": error_id, "code": error_code},
                )
            except Exception as event_exc:
                logger.error(
                    "Could not record terminal run failure event",
                    exc_info=(type(event_exc), event_exc, event_exc.__traceback__),
                    extra={"run_id": run_id, "error_id": error_id},
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
        diagnostic = (node_output.get("metadata") or {}).get("last_error")
        if isinstance(diagnostic, dict) and diagnostic.get("error_id"):
            run_doc.metadata["last_error"] = dict(diagnostic)
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
        self._accumulate_llm_call_metrics(run_doc, node_output)
        incoming_task_metrics = node_output.get("task_execution_metrics") or []
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS and incoming_task_metrics:
            task_metrics = merge_task_execution_metrics(
                run_doc.metadata.get("task_execution_metrics"),
                incoming_task_metrics,
            )
            dependency_map = {
                task.id: list(task.dependencies)
                for task in run_doc.plan
            }
            run_doc.metadata["task_execution_metrics"] = task_metrics
            run_doc.metadata["task_metrics"] = summarize_task_execution_metrics(
                task_metrics,
                dependency_map,
            )

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

    @classmethod
    def _finalize_run_timing_metrics(cls, run_doc: RunDocument) -> None:
        if not settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            return
        finished_at = datetime.now(timezone.utc)
        run_doc.metadata["finished_at"] = finished_at.isoformat()
        run_doc.metadata["workflow_execution_ms"] = round(
            float(run_doc.execution_time_ms or 0.0),
            3,
        )
        queued_at = cls._parse_metric_timestamp(run_doc.metadata.get("queued_at"))
        worker_started_at = cls._parse_metric_timestamp(
            run_doc.metadata.get("worker_started_at")
        )
        request_started_at = cls._parse_metric_timestamp(
            run_doc.metadata.get("request_started_at")
        )
        if queued_at and worker_started_at:
            run_doc.metadata["queue_wait_ms"] = round(
                max(0.0, (worker_started_at - queued_at).total_seconds() * 1000),
                3,
            )
        if request_started_at:
            run_doc.metadata["end_to_end_ms"] = round(
                max(0.0, (finished_at - request_started_at).total_seconds() * 1000),
                3,
            )
        run_doc.metadata["run_timing_metrics"] = {
            key: run_doc.metadata[key]
            for key in (
                "request_started_at",
                "queued_at",
                "worker_started_at",
                "finished_at",
                "queue_wait_ms",
                "workflow_execution_ms",
                "end_to_end_ms",
            )
            if key in run_doc.metadata
        }

    @staticmethod
    def _parse_metric_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _accumulate_llm_call_metrics(
        run_doc: RunDocument,
        source: Dict[str, Any],
    ) -> None:
        if not settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            return
        incoming = source.get("llm_call_metrics") or []
        if not incoming:
            return
        metrics = merge_llm_call_metrics(
            run_doc.metadata.get("llm_call_metrics"),
            incoming,
        )
        run_doc.metadata["llm_call_metrics"] = metrics
        run_doc.metadata["llm_inference_metrics"] = summarize_llm_call_metrics(metrics)

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
            # Typed evidence is already persisted per chunk with exact source excerpts.
            typed_evidence = isinstance(content, dict) and content.get("schema_version") == "1" and (
                "claims" in content or "findings" in content
            )
            source_urls = [] if typed_evidence else self._find_evidence_urls(content)
            for source_url in source_urls:
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
                        source_url=source_url,
                        source_type=self._infer_source_type(source_url),
                        excerpt=serialized[:500],
                        metadata={"node": node_name},
                    )
                )

            if self.artifact_storage is not None:
                artifact_paths = set(self._find_artifact_paths(serialized))
                explicit_artifact_paths = item.get("artifact_paths") or []
                if isinstance(explicit_artifact_paths, list):
                    artifact_paths.update(
                        path.strip()
                        for path in explicit_artifact_paths
                        if isinstance(path, str) and path.strip()
                    )

                for source_path in artifact_paths:
                    if source_path in ingested_paths:
                        continue
                    if not self.artifact_storage.is_generated_file(source_path):
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
    def _find_evidence_urls(content: Any) -> List[str]:
        """Extract deduplicated URLs from raw content, excluding Markdown/JSON delimiters."""
        if isinstance(content, dict):
            values = content.values()
        elif isinstance(content, (list, tuple)):
            values = content
        elif isinstance(content, str):
            urls = set()
            for url in re.findall(r"""https?://[^\s<>"'\\\[\]]+""", content):
                url = url.rstrip(".,;:!?")
                while url.endswith(")") and url.count(")") > url.count("("):
                    url = url[:-1].rstrip(".,;:!?")
                urls.add(url)
            return sorted(urls)
        else:
            return []
        return sorted({url for value in values for url in RunService._find_evidence_urls(value)})

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
            # Tool envelopes may be stored as a JSON string inside result content.
            if isinstance(payload, str):
                payload = json.loads(payload)
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

    async def _enqueue_document(self, document: RunDocument) -> RunDocument:
        request_context = current_context()
        queued_at = datetime.now(timezone.utc)
        command = RunCommand(
            command_id=str(uuid.uuid4()),
            run_id=document.run_id,
            workflow_id=document.flow_id,
            workflow_version_id=document.workflow_version_id,
            requested_by=document.user_id,
            created_at=queued_at,
            metadata={
                "input_data": document.input_data,
                "request_id": request_context.get("request_id"),
                "conversation_id": document.conversation_id,
            },
        )
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            document.metadata["queued_at"] = queued_at.isoformat()
            await self.save_run_doc(document)
        try:
            await self.command_queue.enqueue(command)
        except Exception as exc:
            error_id = str(uuid.uuid4())
            logger.error(
                "Could not enqueue workflow run",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"error_id": error_id, "error_code": "queue_unavailable"},
            )
            document.status = "interrupted"
            document.error_code = "queue_unavailable"
            document.error_message = f"Execution queue is unavailable. Reference: {error_id}."
            self._finalize_run_timing_metrics(document)
            document.metadata["last_error"] = {
                "error_id": error_id,
                "code": "queue_unavailable",
                "category": "dependency",
                "retryable": True,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
            document.logs.append(f"[RunService Queue Error] Run was not enqueued (reference {error_id}).")
            document.updated_at = datetime.now(timezone.utc)
            try:
                await self.save_run_doc(document)
            except Exception as persistence_exc:
                logger.error(
                    "Could not persist queue failure",
                    exc_info=(
                        type(persistence_exc),
                        persistence_exc,
                        persistence_exc.__traceback__,
                    ),
                    extra={"run_id": document.run_id, "error_id": error_id},
                )
            try:
                await self._record_event(
                    document.run_id,
                    "run_failed",
                    phase="execute",
                    status=document.status,
                    payload={
                        "message": document.error_message,
                        "error_id": error_id,
                        "code": "queue_unavailable",
                    },
                )
            except Exception as event_exc:
                logger.error(
                    "Could not publish queue failure event",
                    exc_info=(type(event_exc), event_exc, event_exc.__traceback__),
                    extra={"run_id": document.run_id, "error_id": error_id},
                )
            return document

        try:
            await self._record_event(
                document.run_id,
                "run_progress",
                phase="execute",
                status="queued",
                payload={"command_id": command.command_id},
            )
        except Exception as exc:
            # The command is already in the queue; don't mark the run failed or invite
            # a duplicate enqueue merely because an observability event could not persist.
            logger.error(
                "Queued run progress event could not be recorded",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": document.run_id,
                    "command_id": command.command_id,
                    "error_code": "run_event_persist_failed",
                },
            )
        logger.info(
            "Workflow run enqueued",
            extra={
                "run_id": document.run_id,
                "command_id": command.command_id,
                "run_status": "queued",
            },
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
        except Exception as exc:
            # Durable event replay remains available when live fan-out is down.
            logger.error(
                "Run event live publication failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": event.run_id,
                    "event_type": event.type,
                    "error_code": "run_event_publish_failed",
                },
            )
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

    async def _validate_task_plan(self, plan: Sequence[Task]) -> List[Task]:
        """Apply workflow DAG and catalog permission checks to an execution plan."""

        from app.modules.workflows.contract import normalize_workflow_definition
        from app.modules.workflows.validator import (
            validate_workflow_definition,
            validate_workflow_references,
        )

        if not plan:
            return []
        definition = normalize_workflow_definition(
            {"name": "Run plan", "tasks": [task.model_dump(mode="json") for task in plan]}
        )
        validate_workflow_definition(definition, require_steps=True)
        definition = await validate_workflow_references(definition, self.catalog_repository)
        validate_workflow_definition(definition, require_steps=True)
        return self._normalize_tasks(definition.get("tasks") or [])

    async def _resolve_execution_snapshot(
        self,
        definition: Dict[str, Any],
        plan: Sequence[Task],
        *,
        workflow_version_id: str,
    ) -> Dict[str, Any]:
        """Freeze model routing, prompts, step bindings and active tool metadata for replay."""

        from app.execution.agents.resolver import AGENT_RUNTIME_GUIDANCE, DEFAULT_AGENT_PROFILES
        from app.execution.model_router import model_name_for

        agents_by_ref: Dict[str, Any] = {}
        tools_by_ref: Dict[str, Any] = {}
        if self.catalog_repository is not None and os.getenv("TESTING", "").lower() != "true":
            agents = await self.catalog_repository.list_agents(active_only=True)
            tools = await self.catalog_repository.list_tools(active_only=True)
            agents_by_ref = {
                str(ref): agent
                for agent in agents
                for ref in (agent.id, agent.name)
            }
            tools_by_ref = {
                str(ref): tool
                for tool in tools
                for ref in (tool.id, tool.name)
            }

        agent_profiles: Dict[str, Dict[str, Any]] = {}
        tool_definitions: Dict[str, Dict[str, Any]] = {}
        steps: Dict[str, Dict[str, Any]] = {}
        for task in plan:
            task_key = task.task_key or str(task.id)
            agent = agents_by_ref.get(str(task.agent_id or task.node))
            fallback_key = (task.agent_id or task.node or "worker").strip().lower()
            fallback = DEFAULT_AGENT_PROFILES.get(fallback_key, DEFAULT_AGENT_PROFILES["worker"])
            profile = {
                "name": agent.name if agent is not None else fallback.name,
                "system_prompt": agent.system_prompt if agent is not None else fallback.system_prompt,
                "tool_names": list(agent.tool_names if agent is not None else fallback.tool_names),
                "runtime_name": fallback.runtime_name,
            }
            for reference in (task.agent_id, task.node, profile["name"]):
                if reference:
                    agent_profiles[str(reference)] = profile

            tools_for_task = []
            for reference in task.tool_ids:
                tool = tools_by_ref.get(str(reference))
                if tool is not None:
                    tool_definitions[str(tool.id)] = {
                        "id": tool.id,
                        "name": tool.name,
                        "config_schema": tool.config_schema,
                    }
                    tools_for_task.append({"id": tool.id, "name": tool.name})
            tool_instruction = (
                "Authorized tools for this task (use these exact names only): "
                f"{', '.join(task.tool_names)}."
                if task.tool_names
                else "No tools are available for this task; do not attempt tool calls."
            )
            prompt_sections = [profile["system_prompt"]]
            runtime_guidance = AGENT_RUNTIME_GUIDANCE.get(profile["name"])
            if runtime_guidance:
                prompt_sections.append(runtime_guidance)
            prompt_sections.extend([tool_instruction, f"Your assigned task is: {task.description}."])
            steps[task_key] = {
                "agent_id": task.agent_id,
                "tool_ids": list(task.tool_ids),
                "tool_names": list(task.tool_names),
                "tools": tools_for_task,
                "config": task.config,
                "input_mapping": task.input_mapping,
                "expected_output_type": task.expected_output_type,
                "effective_system_prompt": "\n".join(prompt_sections),
            }

        return {
            "planner_model": model_name_for(InferencePurpose.PLANNER),
            "worker_model": model_name_for(InferencePurpose.WORKER),
            "worker_temperature": 0.2,
            "agent_profiles": agent_profiles,
            "tools": tool_definitions,
            "steps": steps,
            "workflow_version_id": workflow_version_id,
        }

    @staticmethod
    def _plan_revision(plan: Sequence[Task | Dict[str, Any]]) -> str:
        """Hash only authored task data, excluding mutable execution state."""

        authored_fields = (
            "task_key",
            "id",
            "node",
            "agent_id",
            "capability",
            "tool_names",
            "tool_ids",
            "description",
            "dependencies",
            "timeout_seconds",
            "max_iterations",
            "expected_output_type",
            "input_mapping",
            "config",
        )
        canonical_tasks = []
        for value in plan:
            task = value.model_dump(mode="json") if isinstance(value, Task) else value
            canonical = {key: task.get(key) for key in authored_fields}
            canonical["task_key"] = task.get("task_key") or str(task.get("id", ""))
            canonical["tool_names"] = task.get("tool_names") or []
            canonical["tool_ids"] = task.get("tool_ids") or []
            canonical["dependencies"] = task.get("dependencies") or []
            canonical["expected_output_type"] = task.get("expected_output_type") or "raw_data"
            canonical["input_mapping"] = task.get("input_mapping") or {}
            canonical["config"] = task.get("config") or {}
            canonical_tasks.append(canonical)
        serialized = json.dumps(canonical_tasks, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _idempotency_fingerprint(
        *,
        user_id: str,
        workflow_id: str,
        workflow_version_id: str,
        input_data: Dict[str, Any],
        execution_mode: str,
        metadata: Dict[str, Any],
        conversation_id: str | None,
    ) -> str:
        payload = {
            "operation": "create_workflow_run",
            "user_id": user_id,
            "workflow_id": workflow_id,
            "workflow_version_id": workflow_version_id,
            "input_data": input_data,
            "execution_mode": execution_mode,
            "metadata": metadata,
            "conversation_id": conversation_id,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
