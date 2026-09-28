"""Application service for the Build Phase conversation lifecycle."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
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
)
from app.modules.conversations.events import ConversationEvent, ConversationEventPublisher
from app.modules.conversations.ports import ConversationRepository
from app.shared.execution_metrics import merge_execution_timings, summarize_execution_timings
from app.shared.ollama_timing import OllamaTurnTiming, active_ollama_turn


logger = logging.getLogger(__name__)


class ConversationService:
    """Coordinates durable conversation history and plan drafting."""

    def __init__(
        self,
        repository: ConversationRepository,
        execution_port: ExecutionPort,
        event_publisher: ConversationEventPublisher | None = None,
    ) -> None:
        self.repository = repository
        self.execution_port = execution_port
        self.event_publisher = event_publisher

    async def create_conversation(
        self,
        workflow_id: str | None = None,
        title: str | None = None,
        metadata: Dict[str, Any] | None = None,
        user_id: str = "default_user",
    ) -> ConversationRecord:
        now = datetime.utcnow()
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
            created_at=datetime.utcnow(),
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
        conversation.updated_at = datetime.utcnow()
        await self.repository.save(conversation)

        for message in assistant_messages:
            await self.repository.add_message(
                ConversationMessage(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    role="assistant",
                    content=message,
                    created_at=datetime.utcnow(),
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
        """Persist a message and run planning asynchronously for the API contract."""

        turn_started_at = perf_counter()
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None or conversation.status == "archived":
            return None
        self._apply_model_route(conversation, content)
        conversation.updated_at = datetime.utcnow()
        await self.repository.save(conversation)
        user_message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=content,
            created_at=datetime.utcnow(),
        )
        await self.repository.add_message(user_message)
        turn_id = turn_id or str(uuid.uuid4())
        await self._publish(
            ConversationEvent(
                conversation_id=conversation.id,
                turn_id=turn_id,
                type="planning_started",
                payload={"user_message_id": user_message.id},
            )
        )
        asyncio.create_task(
            self._process_turn(
                conversation,
                content,
                turn_id,
                turn_started_at=turn_started_at,
            )
        )
        return {
            "turn_id": turn_id,
            "conversation_id": conversation.id,
            "user_message_id": user_message.id,
            "status": "accepted",
            "events_url": f"/api/v1/conversations/{conversation.id}/events?turn_id={turn_id}",
        }

    async def _process_turn(
        self,
        conversation: ConversationRecord,
        content: str,
        turn_id: str,
        turn_started_at: float,
    ) -> None:
        streamed_content: List[str] = []
        first_token_ttft_ms: float | None = None
        ollama_turn = OllamaTurnTiming(turn_id=turn_id, turn_started_at=turn_started_at)
        timing_token = active_ollama_turn.set(ollama_turn)

        async def publish_assistant_token(token: str) -> None:
            nonlocal first_token_ttft_ms
            if not token:
                return
            if first_token_ttft_ms is None:
                first_token_ttft_ms = round((perf_counter() - turn_started_at) * 1000, 3)
            streamed_content.append(token)
            await self._publish(
                ConversationEvent(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    type="assistant_delta",
                    payload={"content": token},
                )
            )

        try:
            if self._should_continue(conversation):
                result_state = await self.execution_port.continue_conversation(
                    conversation.id,
                    content,
                    metadata=conversation.metadata,
                    on_assistant_token=(
                        publish_assistant_token if self.event_publisher is not None else None
                    ),
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
                    on_assistant_token=(
                        publish_assistant_token if self.event_publisher is not None else None
                    ),
                )

            decision, effective_plan, assistant_messages = self._interpret_planner_result(
                result_state,
                conversation.draft_plan,
            )
            if (
                self.event_publisher is not None
                and assistant_messages
                and first_token_ttft_ms is None
            ):
                # If an adapter does not stream, its first visible token is the completed response.
                first_token_ttft_ms = round((perf_counter() - turn_started_at) * 1000, 3)
            conversation.draft_plan = effective_plan
            conversation.metadata.update(result_state.get("metadata") or {})
            self._accumulate_execution_timings(conversation, result_state)
            conversation.metadata["supervisor_decision"] = decision
            if first_token_ttft_ms is not None or ollama_turn.requests:
                self._record_chat_ttft(
                    conversation,
                    turn_id=turn_id,
                    ttft_ms=first_token_ttft_ms,
                    ollama_requests=ollama_turn.snapshot(),
                )
            conversation.status = "waiting_for_user"
            conversation.updated_at = datetime.utcnow()
            await self.repository.save(conversation)
            await self._publish(
                ConversationEvent(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    type="workflow_draft_updated",
                    payload={
                        "plan": [task.model_dump(mode="json") for task in conversation.draft_plan],
                        "outcome": decision,
                    },
                )
            )
            streamed_message = "".join(streamed_content)
            for message in assistant_messages:
                await self.repository.add_message(
                    ConversationMessage(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation.id,
                        role="assistant",
                        content=message,
                        created_at=datetime.utcnow(),
                    )
                )
                if not streamed_message:
                    await publish_assistant_token(message)
                    streamed_message = message
                elif message.startswith(streamed_message):
                    await publish_assistant_token(message[len(streamed_message):])
                    streamed_message = message
                elif message != streamed_message:
                    await self._publish(
                        ConversationEvent(
                            conversation_id=conversation.id,
                            turn_id=turn_id,
                            type="assistant_replace",
                            payload={"content": message},
                        )
                    )
                    streamed_message = message
            await self._publish(
                ConversationEvent(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    type="planning_completed",
                    payload={"status": conversation.status, "outcome": decision},
                )
            )
        except Exception as exc:
            if first_token_ttft_ms is not None or ollama_turn.requests:
                self._record_chat_ttft(
                    conversation,
                    turn_id=turn_id,
                    ttft_ms=first_token_ttft_ms,
                    ollama_requests=ollama_turn.snapshot(),
                )
                try:
                    await self.repository.save(conversation)
                except Exception:
                    logger.exception("Could not persist chat TTFT sample for turn %s", turn_id)
            await self._publish(
                ConversationEvent(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    type="planning_failed",
                    payload={"message": str(exc) or "Supervisor could not complete this turn."},
                )
            )
        finally:
            active_ollama_turn.reset(timing_token)

    async def stream_events(
        self,
        conversation_id: str,
        turn_id: str | None = None,
    ):
        if self.event_publisher is None:
            return
        async for event in self.event_publisher.subscribe(conversation_id, turn_id):
            payload = event.model_dump(mode="json")
            yield f"id: {event.event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if event.type in {"planning_completed", "planning_failed"}:
                return

    @staticmethod
    def _record_chat_ttft(
        conversation: ConversationRecord,
        *,
        turn_id: str,
        ttft_ms: float | None,
        ollama_requests: list[dict[str, Any]] | None = None,
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
            "measured_at": datetime.utcnow().isoformat(),
        }
        if ollama_requests:
            sample["ollama_requests"] = ollama_requests
        conversation.metadata["chat_ttft_samples"] = [*samples[-49:], sample]

    async def _publish(self, event: ConversationEvent) -> None:
        if self.event_publisher is not None:
            await self.event_publisher.publish(event)

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
        return decision, existing_plan, assistant_messages

    @staticmethod
    def _normalize_tasks(values: List[Task | Dict[str, Any]]) -> List[Task]:
        return [
            value if isinstance(value, Task) else Task.model_validate(value)
            for value in values
        ]
