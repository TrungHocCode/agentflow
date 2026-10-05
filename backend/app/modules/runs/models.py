from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator
from app.core.config import settings
from app.execution.state import Task, LogEntry
from app.shared.execution_metrics import strip_internal_execution_metrics
from app.shared.validation import enforce_json_size

class RunDocument(BaseModel):
    """Durable schema for run lifecycle and execution history."""
    run_id: str
    watchlist_id: Optional[str] = None
    watchlist_revision_id: Optional[str] = None
    flow_id: str
    user_id: str = "default_user"
    conversation_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    plan_revision: Optional[str] = None
    approved_plan_revision: Optional[str] = None
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
    idempotency_fingerprint: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_tokens: int = 0
    execution_time_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class RunStartRequest(BaseModel):
    """Request schema to start a run execution for a flow"""
    flow_id: str
    input_message: Optional[str] = Field(None, max_length=20000)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None:
            enforce_json_size(
                value,
                max_bytes=settings.MAX_REQUEST_METADATA_BYTES,
                field_name="metadata",
            )
        return value


class RunCreateRequest(BaseModel):
    """Create an asynchronous run from the current workflow definition."""

    conversation_id: Optional[str] = None
    workflow_version_id: str = Field(..., min_length=1, max_length=128)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["manual", "scheduled", "experiment"] = "manual"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_data")
    @classmethod
    def validate_input_size(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return enforce_json_size(
            value,
            max_bytes=settings.MAX_RUN_INPUT_BYTES,
            field_name="input_data",
        )

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return enforce_json_size(
            value,
            max_bytes=settings.MAX_REQUEST_METADATA_BYTES,
            field_name="metadata",
        )

class RunApproveRequest(BaseModel):
    """Request schema to approve or reject a pending flow plan"""
    approved: bool = True
    feedback: Optional[str] = None
    plan_revision: Optional[str] = Field(None, min_length=64, max_length=64)

    @field_validator("plan_revision")
    @classmethod
    def validate_plan_revision(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any(character not in "0123456789abcdef" for character in value):
            raise ValueError("plan_revision must be a lowercase SHA-256 digest.")
        return value


class RunChatRequest(BaseModel):
    """Request schema to send a follow-up message to a paused run's conversation"""
    message: str = Field(..., min_length=1, max_length=20000)

class RunResponse(BaseModel):
    """Response schema for run status and details"""
    run_id: str
    watchlist_id: Optional[str] = None
    watchlist_revision_id: Optional[str] = None
    flow_id: str
    user_id: str
    conversation_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    plan_revision: Optional[str] = None
    approved_plan_revision: Optional[str] = None
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

    @field_validator("metadata")
    @classmethod
    def hide_internal_benchmark_metrics(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return strip_internal_execution_metrics(value)
