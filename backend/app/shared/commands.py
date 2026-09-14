"""Commands exchanged between the API and execution worker."""

from datetime import datetime, timezone
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field


class RunCommand(BaseModel):
    """Small queue message referencing a durable run."""

    command_id: str
    run_id: str
    workflow_id: str
    workflow_version_id: str | None = None
    requested_by: str = "default_user"
    command_type: Literal["execute_run"] = "execute_run"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
