from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from app.execution.state import Task, LogEntry

class RunDocument(BaseModel):
    """Durable schema for run lifecycle and execution history."""
    run_id: str
    flow_id: str
    user_id: str = "default_user"
    conversation_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    status: Literal[
        "pending",
        "created",
        "waiting_for_approval",
        "queued",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "abandoned",
    ] = "pending"
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = "pending"
    execution_mode: Literal["manual", "scheduled", "experiment"] = "manual"
    mode: Literal["conversation", "executing"] = "executing"
    current_task: Optional[Task] = None
    plan: List[Task] = Field(default_factory=list)
    logs: List[Any] = Field(default_factory=list)
    result_storage: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    resolved_model_config: Dict[str, Any] = Field(default_factory=dict)
    checkpoint_ref: Optional[str] = None
    idempotency_key: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_tokens: int = 0
    execution_time_ms: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ArtifactDocument(BaseModel):
    """MongoDB Document Schema for Heavy Tool Output Payload / File Artifacts"""
    artifact_id: str
    run_id: str
    task_id: int
    node: str
    name: str
    content_type: str = "text/plain"
    payload: Any
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class RunStartRequest(BaseModel):
    """Request schema to start a run execution for a flow"""
    flow_id: str
    input_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class RunCreateRequest(BaseModel):
    """Create an asynchronous run from the current workflow definition."""

    conversation_id: Optional[str] = None
    input_data: Dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["manual", "scheduled", "experiment"] = "manual"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class RunApproveRequest(BaseModel):
    """Request schema to approve or reject a pending flow plan"""
    approved: bool = True
    feedback: Optional[str] = None


class RunChatRequest(BaseModel):
    """Request schema to send a follow-up message to a paused run's conversation"""
    message: str

class RunResponse(BaseModel):
    """Response schema for run status and details"""
    run_id: str
    flow_id: str
    user_id: str
    conversation_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    status: str
    approval_status: str = "pending"
    execution_mode: str = "manual"
    mode: str
    current_task: Optional[Task] = None
    plan: List[Task] = Field(default_factory=list)
    logs: List[Any] = Field(default_factory=list)
    result_storage: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    resolved_model_config: Dict[str, Any] = Field(default_factory=dict)
    checkpoint_ref: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_tokens: int = 0
    execution_time_ms: float = 0.0
    created_at: datetime
    updated_at: datetime
