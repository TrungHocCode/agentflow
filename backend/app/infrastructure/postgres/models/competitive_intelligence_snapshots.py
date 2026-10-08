"""CI-P3 snapshot persistence; rows are append-only, baseline pointers advance by CAS only."""

from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FetchOutcomeModel(Base):
    __tablename__ = "ci_fetch_outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    bytes_observed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                                                    nullable=True)
    attempted_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("fetch_status IN ('success','failed','blocked','empty')",
                                      name="ck_ci_outcome_status"),
                      Index("ix_ci_outcomes_run_source", "run_id", "source_id"))


class SourceSnapshotModel(Base):
    __tablename__ = "ci_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fetch_outcome_id: Mapped[str] = mapped_column(ForeignKey("ci_fetch_outcomes.id", ondelete="RESTRICT"),
                                                 nullable=False)
    requested_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    captured_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetched_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("quality IN ('eligible','ineligible')", name="ck_ci_snapshot_quality"),
                      Index("ix_ci_snapshots_source_fetched", "source_id", "fetched_at"),
                      Index("ix_ci_snapshots_run_source", "run_id", "source_id"))


class SourceBaselineModel(Base):
    """Current baseline pointer per (source, comparison context, normalization); advanced by CAS only."""

    __tablename__ = "ci_baselines"
    source_id: Mapped[str] = mapped_column(ForeignKey("ci_sources.id", ondelete="RESTRICT"), primary_key=True)
    comparison_context_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    normalization_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("ci_snapshots.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    promoted_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


class RunComparisonModel(Base):
    """One immutable verdict per (run, source); rerunning a run replaces nothing, it conflicts."""

    __tablename__ = "ci_run_comparisons"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("ci_sources.id", ondelete="RESTRICT"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    baseline_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    promotion: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    promoted_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("outcome IN ('baseline_created','no_change','changed','unavailable','rebaseline_required')",
                        name="ck_ci_comparison_outcome"),
        CheckConstraint("quality IN ('complete','partial','insufficient')", name="ck_ci_comparison_quality"),
        CheckConstraint("promotion IN ('pending','promoted','rejected_stale','skipped')",
                        name="ck_ci_comparison_promotion"),
        Index("ix_ci_comparisons_run_source", "run_id", "source_id"))


class ChangeCandidateModel(Base):
    __tablename__ = "ci_change_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    before_snapshot_id: Mapped[str] = mapped_column(ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                                                    nullable=False)
    after_snapshot_id: Mapped[str] = mapped_column(ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                                                   nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)
    before_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    after_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diff_algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    diff_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('added','removed','modified')", name="ck_ci_candidate_kind"),
                      UniqueConstraint("run_id", "source_id", "before_snapshot_id", "after_snapshot_id",
                                       "section", "diff_hash", name="uq_ci_candidate_dedup"),
                      Index("ix_ci_candidates_run_source", "run_id", "source_id"))
