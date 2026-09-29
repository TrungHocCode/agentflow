"""Conversation domain and HTTP models."""

from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.execution.state import Task
from app.shared.execution_metrics import strip_internal_execution_metrics


ConversationStatus = Literal[
    "active",
    "waiting_for_user",
    "completed",
    "archived",
]
ConversationMessageRole = Literal["user", "assistant", "system", "tool"]
ConversationTurnStatus = Literal[
    "queued",
    "running",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


class ConversationMessage(BaseModel):
    """Durable message belonging to a build conversation."""

    id: str
    conversation_id: str
    role: ConversationMessageRole
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ConversationRecord(BaseModel):
    """Conversation metadata and the current workflow draft."""

    id: str
    user_id: str = "default_user"
    workflow_id: str | None = None
    title: str | None = None
    status: ConversationStatus = "active"
    draft_plan: List[Task] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ConversationTurn(BaseModel):
    """Durable lifecycle and outcome of one user planning request."""

    id: str
    conversation_id: str
    user_id: str
    user_message_id: str
    assistant_message_id: str
    input_fingerprint: str
    status: ConversationTurnStatus = "queued"
    outcome: Literal["answer", "clarify", "propose_plan"] | None = None
    assistant_content: str = ""
    plan: List[Task] = Field(default_factory=list)
    error_code: str | None = None
    error_category: str | None = None
    error_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    worker_id: str | None = None
    last_event_sequence: int = 0
    created_at: datetime
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None


class ConversationCreateRequest(BaseModel):
    workflow_id: str | None = None
    title: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    workflow_id: str | None = None
    title: str | None = None
    status: str
    draft_plan: List[Task] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("metadata")
    @classmethod
    def hide_internal_benchmark_metrics(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return strip_internal_execution_metrics(value)


class ConversationMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    turn_id: UUID | None = None


class ConversationTurnResponse(BaseModel):
    conversation: ConversationResponse
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None


class ConversationTurnSnapshot(BaseModel):
    """Public, ownership-safe turn view used for restore and cancellation responses."""

    id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    status: ConversationTurnStatus
    outcome: Literal["answer", "clarify", "propose_plan"] | None = None
    assistant_content: str = ""
    plan: List[Task] = Field(default_factory=list)
    error_code: str | None = None
    error_category: str | None = None
    error_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    last_event_sequence: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
