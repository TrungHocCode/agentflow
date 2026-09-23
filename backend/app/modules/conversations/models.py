"""Conversation domain and HTTP models."""

from datetime import datetime
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from app.execution.state import Task


ConversationStatus = Literal[
    "active",
    "waiting_for_user",
    "completed",
    "archived",
]
ConversationMessageRole = Literal["user", "assistant", "system", "tool"]


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


class ConversationMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationTurnResponse(BaseModel):
    conversation: ConversationResponse
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None
