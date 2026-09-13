"""HTTP DTOs for the legacy flow endpoints and future workflow routes."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.execution.state import FlowDefinition


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., example="Research & Coding Workflow")
    description: Optional[str] = Field(None, example="Flow to research topics and write code")
    definition: Optional[FlowDefinition] = None


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    user_id: str
    definition: Dict[str, Any]
    created_at: Any
    updated_at: Any
