"""Catalog DTOs; SQLAlchemy catalog models live in infrastructure."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentCatalogResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    system_prompt: str
    tool_names: List[str]
    is_active: bool

    class Config:
        from_attributes = True

class ToolCatalogResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    config_schema: Dict[str, Any]
    is_active: bool

    class Config:
        from_attributes = True
