"""SQLAlchemy models for durable run state and event history."""

from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RunModel(Base):
    """Durable run state and compact execution snapshots."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=True)
    watchlist_revision_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    flow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("flows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default="default_user",
    )
    conversation_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_version_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    plan_revision: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_plan_revision: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    approval_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    execution_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="executing")
    current_task: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    plan: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    logs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_storage: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    run_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    input_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resolved_model_config: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    checkpoint_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    idempotency_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_time_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)

    __table_args__ = (
        ForeignKeyConstraint(["watchlist_id", "watchlist_revision_id"],
                             ["ci_watchlist_revisions.watchlist_id", "ci_watchlist_revisions.id"],
                             name="fk_runs_ci_revision", ondelete="RESTRICT"),
        Index("uq_runs_ci_active_watchlist", "watchlist_id", unique=True,
              postgresql_where=text("watchlist_id IS NOT NULL AND status IN "
                                    "('pending','created','waiting_for_approval','queued','running','paused')")),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_runs_user_id_idempotency_key",
        ),
        Index("ix_runs_user_id_created_at", "user_id", "created_at"),
        Index("ix_runs_flow_id_created_at", "flow_id", "created_at"),
        Index("ix_runs_status", "status"),
    )


class RunEventModel(Base):
    """Append-oriented event history used for SSE replay and audit."""

    __tablename__ = "run_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    phase: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_run_sequence", "run_id", "sequence"),
    )
