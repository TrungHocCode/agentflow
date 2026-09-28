"""HTTP DTOs for the legacy flow endpoints and future workflow routes."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., example="Research & Coding Workflow")
    description: Optional[str] = Field(None, example="Flow to research topics and write code")
    # Keep the full canonical contract intact. Runtime Task parsing is a
    # separate adapter and must not discard fields unknown to the executor.
    definition: Optional[Dict[str, Any]] = None


class WorkflowUpdateRequest(BaseModel):
    """Fields that can change while creating a new immutable version."""

    name: Optional[str] = None
    description: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class WorkflowResponse(BaseModel):
    id: str
    version_id: Optional[str] = None
    version_number: Optional[int] = None
    name: str
    description: Optional[str] = None
    user_id: str
    status: str = "active"
    definition: Dict[str, Any]
    created_at: Any
    updated_at: Any
