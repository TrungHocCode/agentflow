"""SQLAlchemy model for workflow aggregates."""

import uuid
from typing import Any, Dict, Optional

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FlowModel(Base):
    """Mutable workflow metadata pointing at its latest definition."""

    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default="default_user",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    definition: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
