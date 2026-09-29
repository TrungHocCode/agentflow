"""Application service for the Build Phase conversation lifecycle."""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import settings
from app.execution.ports import ExecutionPort
from app.execution.model_router import model_name_for, route_conversation
from app.execution.state import State, Task
from app.modules.conversations.models import (
    ConversationMessage,
    ConversationRecord,
    ConversationTurn,
)
from app.modules.conversations.events import ConversationEvent, ConversationEventPublisher
from app.modules.conversations.ports import ConversationRepository
from app.modules.workflows.ports import WorkflowRepository
from app.shared.execution_metrics import merge_execution_timings, summarize_execution_timings
from app.shared.llm_call_metrics import merge_llm_call_metrics, summarize_llm_call_metrics
from app.shared.errors import ApplicationError, PersistenceError
from app.shared.observability import bind_context


logger = logging.getLogger(__name__)


class ConversationService:
    """Coordinates durable conversation history and plan drafting."""

    def __init__(
        self,
        repository: ConversationRepository,
        execution_port: ExecutionPort,
        event_publisher: ConversationEventPublisher | None = None,
        workflow_repository: WorkflowRepository | None = None,
    ) -> None:
        self.repository = repository
        self.execution_port = execution_port
        self.event_publisher = event_publisher
        self.workflow_repository = workflow_repository

    async def create_conversation(
        self,
        workflow_id: str | None = None,
        title: str | None = None,
        metadata: Dict[str, Any] | None = None,
        user_id: str = "default_user",
    ) -> ConversationRecord:
        if workflow_id is not None and self.workflow_repository is not None:
            workflow = await self.workflow_repository.get(workflow_id, user_id)
            if workflow is None or workflow.status != "active":
                from app.shared.errors import ResourceNotFoundError

                raise ResourceNotFoundError(
                    "The linked workflow was not found.",
                    entity="workflow",
                )
        now = datetime.now(timezone.utc)
        conversation = ConversationRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workflow_id=workflow_id,
            title=title,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return await self.repository.create(conversation)

    async def get_conversation(
        self,
        conversation_id: str,
        user_id: str = "default_user",
    ) -> ConversationRecord | None:
        return await self.repository.get(conversation_id, user_id)

    async def list_conversations(
        self,
        user_id: str = "default_user",
        limit: int = 50,
    ) -> List[ConversationRecord]:
        return await self.repository.list(user_id=user_id, limit=limit)

    async def delete_conversation(
        self,
        conversation_id: str,
        user_id: str = "default_user",
    ) -> bool:
        """Delete a conversation and its persisted messages for the owning user."""
        active_turns = [
            turn
            for turn in await self.repository.list_turns(conversation_id, user_id, limit=200)
            if turn.status in {"queued", "running", "cancel_requested"}
        ]
        if active_turns:
            from app.shared.errors import ConflictError

            raise ConflictError("Cancel or wait for the active conversation turn before deleting it.")
        return await self.repository.delete(conversation_id, user_id)

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
    ) -> List[ConversationMessage]:
        return await self.repository.list_messages(conversation_id, limit=limit)

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        user_id: str = "default_user",
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None or conversation.status == "archived":
            return None
        self._apply_model_route(conversation, content)

        user_message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=content,
            created_at=datetime.now(timezone.utc),
        )
        await self.repository.add_message(user_message)

        if self._should_continue(conversation):
            result_state = await self.execution_port.continue_conversation(
                conversation.id,
                content,
                metadata=conversation.metadata,
            )
        else:
            initial_state: State = {
                "messages": [HumanMessage(content=content)],
                "plan": [],
                "current_task": None,
                "logs": [],
                "result_storage": [],
                "mode": "conversation",
                "metadata": conversation.metadata,
            }
            result_state = await self.execution_port.create_plan(
                conversation.id,
                initial_state,
            )

        decision, effective_plan, assistant_messages = self._interpret_planner_result(
            result_state,
            conversation.draft_plan,
        )
        conversation.draft_plan = effective_plan
        conversation.metadata.update(result_state.get("metadata") or {})
        self._accumulate_execution_timings(conversation, result_state)
        conversation.metadata["supervisor_decision"] = decision
        conversation.status = "waiting_for_user"
        conversation.updated_at = datetime.now(timezone.utc)
        await self.repository.save(conversation)

        for message in assistant_messages:
            await self.repository.add_message(
                ConversationMessage(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    role="assistant",
                    content=message,
                    created_at=datetime.now(timezone.utc),
                )
            )
        return conversation

    async def start_message(
        self,
        conversation_id: str,
        content: str,
        user_id: str = "default_user",
        turn_id: str | None = None,
    ) -> Dict[str, Any] | None:
        """Atomically persist a user request as a durable worker-queue turn."""

        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None or conversation.status == "archived":
            return None
        self._apply_model_route(conversation, content)
        turn_id = turn_id or str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)
        conversation.updated_at = created_at
        user_message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=content,
            metadata={"turn_id": turn_id},
            created_at=created_at,
        )
        assistant_message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            content="",
            metadata={"turn_id": turn_id, "status": "queued"},
            created_at=created_at + timedelta(microseconds=1),
        )
        turn = ConversationTurn(
            id=turn_id,
            conversation_id=conversation.id,
            user_id=user_id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            input_fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            status="queued",
            created_at=created_at,
        )
        accepted_turn = await self.repository.accept_turn(
            conversation,
            turn,
            user_message,
            assistant_message,
        )
        accepted_events = await self.repository.list_turn_events(
            conversation.id,
            accepted_turn.id,
            after_sequence=0,
            limit=1,
        )
        for event in accepted_events:
            await self._publish(event)
        return {
            "turn_id": accepted_turn.id,
            "conversation_id": conversation.id,
            "user_message_id": accepted_turn.user_message_id,
            "assistant_message_id": accepted_turn.assistant_message_id,
            "status": "accepted",
            "turn_status": accepted_turn.status,
            "last_event_sequence": accepted_turn.last_event_sequence,
            "events_url": (
                f"/api/v1/conversations/{conversation.id}/events?turn_id={accepted_turn.id}"
            ),
        }

    async def process_next_turn(self, worker_id: str = "agentflow-worker") -> bool:
        """Claim and execute one durable conversation turn in the shared worker process."""

        now = datetime.now(timezone.utc)
        recovered = await self.repository.recover_stale_turns(
            stale_before=now - timedelta(seconds=settings.CONVERSATION_TURN_STALE_SECONDS),
            queue_expired_before=now - timedelta(
                seconds=settings.CONVERSATION_TURN_MAX_QUEUE_WAIT_SECONDS
            ),
        )
        for stale_turn in recovered:
            events = await self.repository.list_turn_events(
                stale_turn.conversation_id,
                stale_turn.id,
                after_sequence=max(stale_turn.last_event_sequence - 1, 0),
                limit=1,
            )
            for event in events:
                await self._publish(event)

        turn = await self.repository.claim_next_turn(worker_id)
        if turn is None:
            return False
        logger.info(
            "Claimed queued conversation turn",
            extra={"turn_id": turn.id, "worker_id": worker_id},
        )
        with bind_context(conversation_id=turn.conversation_id, turn_id=turn.id):
            started_events = await self.repository.list_turn_events(
                turn.conversation_id,
                turn.id,
                after_sequence=max(turn.last_event_sequence - 1, 0),
                limit=1,
            )
            for event in started_events:
                await self._publish(event)
            conversation = await self.get_conversation(turn.conversation_id, turn.user_id)
            if conversation is None:
                await self._finalize_turn_failure(
                    turn,
                    None,
                    RuntimeError("Conversation disappeared before planning began."),
                    None,
                    perf_counter(),
                )
                return True
            await self._execute_claimed_turn(turn, conversation)
        return True

    async def _execute_claimed_turn(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord,
    ) -> None:
        started = perf_counter()
        cancel_requested = asyncio.Event()
        task = asyncio.create_task(self._plan_durable_turn(turn, conversation, started))

        async def monitor_turn() -> None:
            last_heartbeat = perf_counter()
            while not task.done():
                await asyncio.sleep(max(settings.CONVERSATION_TURN_POLL_SECONDS, 0.1))
                current = await self.repository.get_turn(
                    turn.conversation_id,
                    turn.id,
                    turn.user_id,
                )
                if current is None:
                    continue
                if current.status == "cancel_requested":
                    cancel_requested.set()
                    task.cancel()
                    return
                if perf_counter() - last_heartbeat >= max(
                    settings.CONVERSATION_TURN_HEARTBEAT_SECONDS,
                    1,
                ):
                    await self.repository.heartbeat_turn(turn.id, turn.worker_id or "")
                    last_heartbeat = perf_counter()

        monitor = asyncio.create_task(monitor_turn())
        try:
            await asyncio.wait_for(task, timeout=settings.CONVERSATION_TURN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._finalize_turn_failure(
                turn,
                conversation,
                TimeoutError("Conversation planning exceeded its time limit."),
                None,
                started,
            )
        except asyncio.CancelledError:
            if not cancel_requested.is_set():
                raise
            await self._finalize_cancelled_turn(turn, conversation)
        finally:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

    async def _plan_durable_turn(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord,
        started: float,
    ) -> None:
        streamed_content: List[str] = []
        pending_delta: List[str] = []
        pending_since = perf_counter()
        first_token_ttft_ms: float | None = None
        result_state: State | None = None

        async def flush_delta(force: bool = False) -> None:
            nonlocal pending_since
            content = "".join(pending_delta)
            if not content:
                return
            if not force and len(content) < settings.CONVERSATION_DELTA_FLUSH_CHARS and (
                perf_counter() - pending_since < settings.CONVERSATION_DELTA_FLUSH_INTERVAL_SECONDS
            ):
                return
            pending_delta.clear()
            pending_since = perf_counter()
            await self._record_and_publish_event(
                turn.id,
                "assistant_delta",
                {"assistant_message_id": turn.assistant_message_id, "content": content},
            )

        async def publish_assistant_token(token: str) -> None:
            nonlocal first_token_ttft_ms
            if not token:
                return
            if first_token_ttft_ms is None:
                first_token_ttft_ms = round((perf_counter() - started) * 1000, 3)
            streamed_content.append(token)
            pending_delta.append(token)
            await flush_delta()

        try:
            history = await self.repository.list_messages(turn.conversation_id, limit=200)
            llm_messages = []
            for message in history[-10:]:
                if not message.content:
                    continue
                if message.role == "user":
                    llm_messages.append(HumanMessage(content=message.content))
                elif message.role == "assistant":
                    llm_messages.append(AIMessage(content=message.content))
            initial_state: State = {
                "messages": llm_messages,
                "plan": [],
                "current_task": None,
                "logs": [],
                "result_storage": [],
                "mode": "conversation",
                "metadata": conversation.metadata,
            }
            result_state = await self.execution_port.create_plan(
                turn.id,
                initial_state,
                on_assistant_token=publish_assistant_token,
            )
            decision, effective_plan, assistant_messages = self._interpret_planner_result(
                result_state,
                [],
            )
            self._accumulate_execution_timings(conversation, result_state)
            self._accumulate_llm_call_metrics(conversation, result_state)
            conversation.metadata.update(result_state.get("metadata") or {})
            conversation.draft_plan = effective_plan
            conversation.metadata["supervisor_decision"] = decision
            assistant_content = assistant_messages[-1]
            if first_token_ttft_ms is None:
                first_token_ttft_ms = round((perf_counter() - started) * 1000, 3)
            self._record_chat_ttft(conversation, turn_id=turn.id, ttft_ms=first_token_ttft_ms)
            completed_at = datetime.now(timezone.utc)
            conversation.status = "waiting_for_user"
            conversation.updated_at = completed_at
            conversation.metadata["last_turn"] = {
                "turn_id": turn.id,
                "status": "completed",
                "outcome": decision,
                "completed_at": completed_at.isoformat(),
            }
            if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
                conversation.metadata["last_turn"]["planning_duration_ms"] = round(
                    (perf_counter() - started) * 1000,
                    3,
                )
            turn.status = "completed"
            turn.outcome = decision
            turn.assistant_content = assistant_content
            turn.plan = effective_plan
            turn.completed_at = completed_at
            assistant_message = await self._assistant_turn_message(
                turn,
                assistant_content,
                status="completed",
                outcome=decision,
            )
            await flush_delta(force=True)
            events = await self.repository.finalize_turn(
                turn,
                conversation,
                assistant_message,
                [
                    (
                        "assistant_message",
                        {"assistant_message_id": turn.assistant_message_id, "content": assistant_content},
                    ),
                    (
                        "workflow_draft",
                        {
                            "plan": [task.model_dump(mode="json") for task in effective_plan],
                            "outcome": decision,
                        },
                    ),
                    (
                        "turn_completed",
                        {"status": "completed", "outcome": decision},
                    ),
                ],
            )
            for event in events:
                await self._publish(event)
            logger.info(
                "Conversation turn completed",
                extra={"turn_id": turn.id, "outcome": decision},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if result_state is not None:
                self._accumulate_execution_timings(conversation, result_state)
                self._accumulate_llm_call_metrics(conversation, result_state)
            if first_token_ttft_ms is not None:
                self._record_chat_ttft(conversation, turn_id=turn.id, ttft_ms=first_token_ttft_ms)
            await flush_delta(force=True)
            await self._finalize_turn_failure(turn, conversation, exc, result_state, started)

    async def _finalize_turn_failure(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord | None,
        exc: Exception,
        result_state: State | None,
        started: float,
    ) -> None:
        if conversation is None:
            conversation = await self.get_conversation(turn.conversation_id, turn.user_id)
        if conversation is None:
            logger.error("Conversation disappeared while finalizing a turn", extra={"turn_id": turn.id})
            return
        planner_metadata = (result_state or {}).get("metadata") or {}
        error_id = (
            planner_metadata.get("planning_error_id")
            or (exc.error_id if isinstance(exc, ApplicationError) else None)
            or str(uuid.uuid4())
        )
        if isinstance(exc, TimeoutError):
            error_code, category = "conversation_turn_timeout", "timeout"
            safe_message, retryable = "Yêu cầu mất quá nhiều thời gian và đã dừng. Vui lòng thử lại.", True
        elif planner_metadata.get("planning_failed"):
            error_code = planner_metadata.get("planning_error_code", "planning_failed")
            category = planner_metadata.get("planning_error_category", "model")
            safe_message, retryable = "Không thể tạo phản hồi hợp lệ lúc này. Vui lòng thử lại.", category != "configuration"
        elif isinstance(exc, PersistenceError):
            error_code, category = exc.code, exc.category
            safe_message, retryable = "Không thể lưu trạng thái hội thoại. Vui lòng thử lại sau.", True
        elif isinstance(exc, (ConnectionError, OSError)):
            error_code, category = "llm_unavailable", "dependency"
            safe_message, retryable = "Mô hình hiện không phản hồi được. Vui lòng thử lại sau.", True
        else:
            error_code = exc.code if isinstance(exc, ApplicationError) else "planning_failed"
            category = exc.category if isinstance(exc, ApplicationError) else "model"
            safe_message = "Không thể tạo kế hoạch hoặc câu trả lời hợp lệ. Vui lòng thử lại."
            retryable = isinstance(exc, ApplicationError) and exc.retryable
        logger.error(
            "Conversation turn failed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"turn_id": turn.id, "error_id": error_id, "error_code": error_code},
        )
        conversation.draft_plan = []
        conversation.metadata.pop("supervisor_decision", None)
        conversation.metadata.pop("planning_failed", None)
        conversation.metadata.pop("planning_error_message", None)
        completed_at = datetime.now(timezone.utc)
        conversation.metadata["last_turn"] = {
            "turn_id": turn.id,
            "status": "failed",
            "error_id": error_id,
            "error_code": error_code,
            "category": category,
            "retryable": retryable,
            "completed_at": completed_at.isoformat(),
        }
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            conversation.metadata["last_turn"]["planning_duration_ms"] = round(
                (perf_counter() - started) * 1000,
                3,
            )
        conversation.status = "waiting_for_user"
        conversation.updated_at = completed_at
        turn.status = "failed"
        turn.error_id = error_id
        turn.error_code = error_code
        turn.error_category = category
        turn.error_message = safe_message
        turn.retryable = retryable
        turn.completed_at = completed_at
        turn.assistant_content = safe_message
        assistant_message = await self._assistant_turn_message(
            turn,
            safe_message,
            status="failed",
            error_id=error_id,
        )
        try:
            events = await self.repository.finalize_turn(
                turn,
                conversation,
                assistant_message,
                [
                    (
                        "assistant_message",
                        {"assistant_message_id": turn.assistant_message_id, "content": safe_message},
                    ),
                    (
                        "turn_failed",
                        {
                            "status": "failed",
                            "message": safe_message,
                            "error_id": error_id,
                            "code": error_code,
                            "category": category,
                            "retryable": retryable,
                        },
                    ),
                ],
            )
            for event in events:
                await self._publish(event)
        except Exception:
            logger.exception(
                "Could not persist terminal conversation failure",
                extra={"turn_id": turn.id, "error_id": error_id},
            )

    async def _finalize_cancelled_turn(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord,
    ) -> None:
        now = datetime.now(timezone.utc)
        turn.status = "cancelled"
        turn.completed_at = now
        turn.assistant_content = "Yêu cầu đã được hủy."
        conversation.status = "waiting_for_user"
        conversation.updated_at = now
        conversation.metadata["last_turn"] = {
            "turn_id": turn.id,
            "status": "cancelled",
            "completed_at": now.isoformat(),
        }
        assistant_message = await self._assistant_turn_message(
            turn,
            turn.assistant_content,
            status="cancelled",
        )
        events = await self.repository.finalize_turn(
            turn,
            conversation,
            assistant_message,
            [
                (
                    "assistant_message",
                    {"assistant_message_id": turn.assistant_message_id, "content": turn.assistant_content},
                ),
                ("turn_cancelled", {"status": "cancelled"}),
            ],
        )
        for event in events:
            await self._publish(event)

    async def _assistant_turn_message(
        self,
        turn: ConversationTurn,
        content: str,
        *,
        status: str,
        outcome: str | None = None,
        error_id: str | None = None,
    ) -> ConversationMessage:
        current = next(
            (
                message
                for message in await self.repository.list_messages(turn.conversation_id, limit=1000)
                if message.id == turn.assistant_message_id
            ),
            None,
        )
        metadata = dict(current.metadata if current else {})
        metadata.update({"turn_id": turn.id, "status": status})
        if outcome:
            metadata["outcome"] = outcome
        if error_id:
            metadata["error_id"] = error_id
        return ConversationMessage(
            id=turn.assistant_message_id,
            conversation_id=turn.conversation_id,
            role="assistant",
            content=content,
            metadata=metadata,
            created_at=current.created_at if current else turn.created_at,
        )

    async def cancel_turn(
        self,
        conversation_id: str,
        turn_id: str,
        user_id: str = "default_user",
    ) -> ConversationTurn | None:
        turn = await self.repository.request_turn_cancel(conversation_id, turn_id, user_id)
        if turn is None:
            return None
        cancel_events = await self.repository.list_turn_events(
            conversation_id,
            turn_id,
            after_sequence=max(turn.last_event_sequence - 1, 0),
            limit=1,
        )
        for event in cancel_events:
            await self._publish(event)
        if turn.status == "cancel_requested" and turn.started_at is None:
            conversation = await self.get_conversation(conversation_id, user_id)
            if conversation:
                await self._finalize_cancelled_turn(turn, conversation)
                turn = await self.repository.get_turn(conversation_id, turn_id, user_id) or turn
        return turn

    async def list_turns(
        self,
        conversation_id: str,
        user_id: str = "default_user",
        limit: int = 50,
    ) -> List[ConversationTurn]:
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None:
            return []
        return await self.repository.list_turns(conversation_id, user_id, limit)

    async def stream_events(
        self,
        conversation_id: str,
        turn_id: str | None = None,
        after_sequence: int = 0,
        user_id: str = "default_user",
    ):
        if turn_id is None:
            return
        subscription = (
            self.event_publisher.subscribe(conversation_id, turn_id)
            if self.event_publisher is not None
            else None
        )
        notification_task = None
        cursor = max(after_sequence, 0)
        last_keepalive_at = perf_counter()
        terminal_events = {"turn_completed", "turn_failed", "turn_cancelled", "turn_interrupted"}
        try:
            ready_event = None
            if subscription is not None:
                try:
                    ready_event = await anext(subscription)
                except Exception:
                    logger.warning(
                        "Conversation event fan-out unavailable; using durable event polling",
                        extra={"conversation_id": conversation_id, "turn_id": turn_id},
                        exc_info=True,
                    )
                    try:
                        await subscription.aclose()
                    except Exception:
                        logger.debug("Could not close failed conversation event subscription", exc_info=True)
                    subscription = None
            if ready_event is None:
                ready_event = ConversationEvent(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    type="stream_ready",
                    sequence=cursor,
                )
            ready_payload = json.dumps(ready_event.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {cursor}\ndata: {ready_payload}\n\n"
            if subscription is not None:
                notification_task = asyncio.create_task(anext(subscription))
            while True:
                events = await self.repository.list_turn_events(
                    conversation_id,
                    turn_id,
                    after_sequence=cursor,
                    limit=500,
                )
                for event in events:
                    cursor = event.sequence
                    payload = event.model_dump(mode="json")
                    yield f"id: {event.sequence}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if event.type in terminal_events:
                        return
                if not events:
                    current_turn = await self.repository.get_turn(
                        conversation_id,
                        turn_id,
                        user_id,
                    )
                    if current_turn is not None and current_turn.status in {
                        "completed", "failed", "cancelled", "interrupted"
                    }:
                        return
                    if perf_counter() - last_keepalive_at >= 15:
                        yield ": keep-alive\n\n"
                        last_keepalive_at = perf_counter()
                if notification_task is None:
                    await asyncio.sleep(settings.CONVERSATION_EVENT_POLL_SECONDS)
                else:
                    done, _ = await asyncio.wait(
                        {notification_task},
                        timeout=settings.CONVERSATION_EVENT_POLL_SECONDS,
                    )
                    if done:
                        try:
                            notification_task.result()
                        except StopAsyncIteration:
                            notification_task = None
                            subscription = None
                        except Exception:
                            logger.warning(
                                "Conversation event fan-out interrupted; using durable event polling",
                                extra={"conversation_id": conversation_id, "turn_id": turn_id},
                                exc_info=True,
                            )
                            notification_task = None
                            try:
                                await subscription.aclose()
                            except Exception:
                                logger.debug(
                                    "Could not close interrupted conversation event subscription",
                                    exc_info=True,
                                )
                            subscription = None
                        else:
                            notification_task = asyncio.create_task(anext(subscription))
        finally:
            if notification_task is not None:
                notification_task.cancel()
                await asyncio.gather(notification_task, return_exceptions=True)
            if subscription is not None:
                await subscription.aclose()

    @staticmethod
    def _record_chat_ttft(
        conversation: ConversationRecord,
        *,
        turn_id: str,
        ttft_ms: float,
    ) -> None:
        """Persist a bounded, content-free server TTFT sample for internal evaluation."""

        samples = [
            sample
            for sample in conversation.metadata.get("chat_ttft_samples", [])
            if sample.get("turn_id") != turn_id
        ]
        sample = {
            "turn_id": turn_id,
            "ttft_ms": ttft_ms,
            "model": model_name_for(
                conversation.metadata.get("inference_purpose", "planner")
            ),
            "measured_at": datetime.now(timezone.utc).isoformat(),
        }
        conversation.metadata["chat_ttft_samples"] = [*samples[-49:], sample]

    async def _publish(self, event: ConversationEvent) -> None:
        if self.event_publisher is not None:
            try:
                await self.event_publisher.publish(event)
            except Exception as exc:
                logger.error(
                    "Conversation event publication failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"error_code": "conversation_event_publish_failed", "event_type": event.type},
                )

    async def _record_and_publish_event(
        self,
        turn_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> ConversationEvent:
        event = await self.repository.append_turn_event(turn_id, event_type, payload)
        await self._publish(event)
        return event

    @staticmethod
    def _accumulate_execution_timings(
        conversation: ConversationRecord,
        result_state: State,
    ) -> None:
        if not settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            return
        incoming = result_state.get("execution_timings") or []
        if not incoming:
            return
        timings = merge_execution_timings(
            conversation.metadata.get("execution_timings"),
            incoming,
        )
        conversation.metadata["execution_timings"] = timings
        conversation.metadata["execution_metrics"] = summarize_execution_timings(timings)

    @staticmethod
    def _accumulate_llm_call_metrics(
        conversation: ConversationRecord,
        result_state: State,
    ) -> None:
        if not settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            return
        incoming = result_state.get("llm_call_metrics") or []
        if not incoming:
            return
        metrics = merge_llm_call_metrics(
            conversation.metadata.get("llm_call_metrics"),
            incoming,
        )
        conversation.metadata["llm_call_metrics"] = metrics
        conversation.metadata["llm_inference_metrics"] = summarize_llm_call_metrics(metrics)

    @staticmethod
    def _apply_model_route(conversation: ConversationRecord, content: str) -> None:
        """Choose a model profile internally for this conversation turn."""
        metadata = dict(conversation.metadata or {})
        purpose = route_conversation(
            content,
            has_pending_plan=bool(conversation.draft_plan),
            previous_decision=metadata.get("supervisor_decision"),
        )
        # Remove the old client-selected model field so stale conversations or
        # older clients cannot override the server's routing policy.
        metadata.pop("model_name", None)
        metadata.update({
            "use_llm": True,
            "inference_purpose": purpose.value,
        })
        conversation.metadata = metadata

    @staticmethod
    def _assistant_messages(messages: List[Any]) -> List[str]:
        result: List[str] = []
        for message in messages:
            if isinstance(message, AIMessage):
                result.append(str(message.content))
            elif isinstance(message, dict) and message.get("role") == "assistant":
                result.append(str(message.get("content", "")))
        return [message for message in result if message][-1:]

    @staticmethod
    def _should_continue(conversation: ConversationRecord) -> bool:
        """Resume the same planner thread after a proposal or a conversational turn."""

        return bool(conversation.draft_plan) or conversation.metadata.get(
            "supervisor_decision"
        ) in {"clarify", "answer"}

    @classmethod
    def _interpret_planner_result(
        cls,
        result_state: State,
        existing_plan: List[Task],
    ) -> tuple[str, List[Task], List[str]]:
        metadata = result_state.get("metadata") or {}
        if metadata.get("planning_failed"):
            raise RuntimeError(
                metadata.get("planning_error_message")
                or "Supervisor could not create a valid response."
            )

        decision = metadata.get("supervisor_decision")
        plan = cls._normalize_tasks(result_state.get("plan") or [])
        assistant_messages = cls._assistant_messages(result_state.get("messages") or [])

        # Keep deterministic test adapters and the legacy non-LLM planner usable.
        if decision is None and plan:
            decision = "propose_plan"
        if decision not in {"clarify", "propose_plan", "answer"}:
            raise RuntimeError("Supervisor returned an unknown or missing decision.")
        if not assistant_messages:
            raise RuntimeError("Supervisor returned a blank assistant message.")

        if decision == "propose_plan":
            if not plan:
                raise RuntimeError("Supervisor proposed a workflow without any tasks.")
            return decision, plan, assistant_messages
        if decision == "clarify":
            if plan:
                raise RuntimeError("A clarification response must not contain a workflow plan.")
            return decision, [], assistant_messages
        # A fresh answer is not authorization to keep or approve a prior draft.
        return decision, [], assistant_messages

    @staticmethod
    def _normalize_tasks(values: List[Task | Dict[str, Any]]) -> List[Task]:
        return [
            value if isinstance(value, Task) else Task.model_validate(value)
            for value in values
        ]
