import uuid
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, JSON, Text, Boolean
from app.db.base import Base

# Agent Catalog ORM Model
class AgentCatalogModel(Base):
    __tablename__ = "agent_catalog"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tool_names: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

# Tool Catalog ORM Model
class ToolCatalogModel(Base):
    __tablename__ = "tool_catalog"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

# Schemas
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
