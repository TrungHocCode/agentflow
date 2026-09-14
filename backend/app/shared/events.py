"""Stable event envelope shared by execution and transport adapters."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ExecutionEvent(BaseModel):
    """User-safe progress event; never contains private chain-of-thought."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    sequence: int = 0
    schema_version: str = "1"
    type: str
    task_id: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    label: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
