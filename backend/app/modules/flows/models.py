"""Legacy flow DTOs kept separate from database persistence models."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.execution.state import FlowDefinition


class FlowCreate(BaseModel):
    name: str = Field(..., example="Research & Coding Workflow")
    description: Optional[str] = Field(None, example="Flow to research topics and write code")
    definition: Optional[FlowDefinition] = None

class FlowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    user_id: str
    status: str = "active"
    definition: Dict[str, Any]
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True
