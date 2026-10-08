"""CI-P5 typed intelligence brief contracts.

A brief is a deterministic presentation of approved structured content: per-source
coverage, observation window, baseline references, findings with verification labels,
rationale, evidence, conflicts, limitations and advisory actions. It never claims a
completed run has all findings verified.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.competitive_intelligence.models import Contract

BRIEF_SCHEMA_VERSION = "1"
BriefOutcome = Literal["baseline_created", "no_change", "changes_detected", "partial",
                       "rebaseline_required", "unavailable"]
BriefQuality = Literal["complete", "partial", "insufficient"]
FindingVerification = Literal["observed", "unresolved"]


class BriefFinding(Contract):
    """One brief finding; verification stays quotation-level, never semantic proof."""

    id: UUID
    text: str = Field(min_length=1, max_length=1000)
    verification: FindingVerification
    rationale: str = Field(default="", max_length=2000)
    candidate_id: UUID | None = None
    before_snapshot_id: UUID | None = None
    after_snapshot_id: UUID | None = None
    evidence_offsets: list[int] = Field(default_factory=list, max_length=8)


class BriefSourceCoverage(Contract):
    source_id: UUID
    baseline_snapshot_id: UUID | None = None
    current_snapshot_id: UUID | None = None
    outcome: str = Field(min_length=1, max_length=32)
    quality: BriefQuality
    reason_codes: list[str] = Field(default_factory=list, max_length=16)
    observed_at: datetime


class IntelligenceBrief(Contract):
    id: UUID
    watchlist_id: UUID
    revision_id: UUID
    run_id: UUID
    schema_version: Literal["1"] = "1"
    outcome: BriefOutcome
    quality: BriefQuality
    summary: str = Field(min_length=1, max_length=8000)
    findings: list[BriefFinding] = Field(default_factory=list, max_length=50)
    source_coverage: list[BriefSourceCoverage] = Field(default_factory=list, max_length=40)
    conflicts: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(default_factory=list, max_length=16)
    advisory_actions: list[str] = Field(default_factory=list, max_length=16)
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_uri: str = Field(max_length=512)
    artifact_hash: str = Field(min_length=64, max_length=64)
    observed_from: datetime
    observed_to: datetime
    created_at: datetime


class BuildBriefRequest(Contract):
    run_id: UUID
    revision_id: UUID
