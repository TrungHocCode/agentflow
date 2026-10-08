"""CI-P4 investigation round persistence; accepted rounds are immutable DAG deltas."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoundModel(Base):
    __tablename__ = "ci_investigation_rounds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_round_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    decided_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('proposed','accepted','rejected','superseded','completed','interrupted')",
                        name="ck_ci_round_status"),
        UniqueConstraint("run_id", "round_number", name="uq_ci_round_run_number"),
        Index("ix_ci_rounds_run", "run_id", "round_number"))
