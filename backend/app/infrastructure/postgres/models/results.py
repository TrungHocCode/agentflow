"""PostgreSQL metadata models for structured research output."""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResultModel(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    task_execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    result_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[Any] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    result_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    __table_args__ = (Index("ix_results_run_created_at", "run_id", "created_at"),)


class EvidenceModel(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    task_execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    evidence_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    collected_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (Index("ix_evidence_run_collected_at", "run_id", "collected_at"),)


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    task_execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    artifact_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    __table_args__ = (Index("ix_artifacts_run_created_at", "run_id", "created_at"),)
