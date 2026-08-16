from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from app.execution.state import Task, LogEntry

class RunDocument(BaseModel):
    """MongoDB Document Schema for Run Execution History"""
    run_id: str
    flow_id: str
    user_id: str = "default_user"
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    mode: Literal["conversation", "executing"] = "executing"
    current_task: Optional[Task] = None
    plan: List[Task] = Field(default_factory=list)
    logs: List[Any] = Field(default_factory=list)
    result_storage: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
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
