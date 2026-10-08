"""CI-P5 intelligence brief persistence; briefs are immutable once recorded."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BriefModel(Base):
    __tablename__ = "ci_briefs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False, default="1")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(String(8000), nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    source_coverage: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    conflicts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    advisory_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    artifact_id: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_from: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_to: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("outcome IN ('baseline_created','no_change','changes_detected','partial',"
                        "'rebaseline_required','unavailable')", name="ck_ci_brief_outcome"),
        CheckConstraint("quality IN ('complete','partial','insufficient')", name="ck_ci_brief_quality"),
        Index("ix_ci_briefs_watchlist_created", "watchlist_id", "created_at"))
