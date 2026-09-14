"""SQLAlchemy model for immutable workflow versions."""

from typing import Any, Dict

from sqlalchemy import ForeignKey, JSON, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkflowVersionModel(Base):
    """Immutable version of a workflow definition."""

    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("flows.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="published")
    definition: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default="default_user",
    )

    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "version_number",
            name="uq_workflow_versions_workflow_number",
        ),
        Index(
            "ix_workflow_versions_workflow_number",
            "workflow_id",
            "version_number",
        ),
    )
