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
    definition: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
