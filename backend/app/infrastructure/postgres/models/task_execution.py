"""Durable identity for a workflow step inside one run."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskExecutionModel(Base):
    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    workflow_step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_steps.id", ondelete="RESTRICT"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    agent_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    resolved_tool_ids: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    input_reference: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("results.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "workflow_step_id", name="uq_task_executions_run_step"),
        Index("ix_task_executions_run_status", "run_id", "status"),
    )
