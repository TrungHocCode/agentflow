"""Workflow domain records independent from SQLAlchemy persistence models."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRecord(BaseModel):
    """Current workflow representation returned by the application layer."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    version_id: str | None = None
    version_number: int | None = None
    name: str
    description: Optional[str] = None
    user_id: str
    status: str = "active"
    definition: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class WorkflowVersionRecord(BaseModel):
    """Immutable workflow version exposed by the application layer."""

    id: str
    workflow_id: str
    version_number: int
    status: str = "published"
    definition: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    created_by: str = "default_user"
    created_at: datetime
