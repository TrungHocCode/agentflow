"""Catalog domain records independent from SQLAlchemy persistence models."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentDefinition(BaseModel):
    """Public agent definition consumed by planning and execution."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    system_prompt: str
    tool_names: List[str] = Field(default_factory=list)
    is_active: bool = True

class ToolDefinition(BaseModel):
    """Public tool metadata; this context does not execute the tool."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
