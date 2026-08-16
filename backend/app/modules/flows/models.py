import uuid
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, JSON, Text
from app.db.base import Base
from app.execution.state import FlowDefinition

# SQLAlchemy ORM Model (PostgreSQL)
class FlowModel(Base):
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default_user")
    definition: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

# Pydantic Request/Response Schemas
class FlowCreate(BaseModel):
    name: str = Field(..., example="Research & Coding Workflow")
    description: Optional[str] = Field(None, example="Flow to research topics and write code")
    definition: Optional[FlowDefinition] = None

class FlowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    user_id: str
    definition: Dict[str, Any]
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True
