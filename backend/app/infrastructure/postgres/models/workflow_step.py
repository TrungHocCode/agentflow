"""Normalized workflow-version steps and their same-version relations."""

from typing import Any, Dict, Optional

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkflowStepModel(Base):
    __tablename__ = "workflow_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("workflow_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Transitional free-form reference: older snapshots contain catalog names,
    # while canonical API versions use catalog UUIDs.
    agent_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    config: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_mapping: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "workflow_version_id",
            "task_key",
            name="uq_workflow_steps_version_task_key",
        ),
        Index("ix_workflow_steps_version_position", "workflow_version_id", "position"),
    )


class WorkflowStepDependencyModel(Base):
    __tablename__ = "workflow_step_dependencies"

    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_steps.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_steps.id", ondelete="CASCADE"), primary_key=True
    )


class WorkflowStepToolModel(Base):
    __tablename__ = "workflow_step_tools"

    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_steps.id", ondelete="CASCADE"), primary_key=True
    )
    tool_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    config_override: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
