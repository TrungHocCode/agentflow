"""CI-P3 snapshot and change-detection contracts.

A snapshot is a mechanical observation of a publisher page at one fetch moment.
A change candidate is deterministic diff output, never a verified business finding
(verification belongs to the CI-P4 Evidence Verifier role).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.modules.competitive_intelligence.models import Contract

NORMALIZATION_VERSION = "ci-normalize-v1"
EXTRACTOR_VERSION = "ci-extract-v1"
DIFF_ALGORITHM_VERSION = "ci-section-diff-v1"
CAPTURE_POLICY = "public_get_only"
MIN_ELIGIBLE_CHARS = 200
MAX_EXCERPT_CHARS = 800
MAX_SPAN_CHARS = 16000
MAX_SECTIONS = 200

FetchStatus = Literal["success", "failed", "blocked", "empty"]
SnapshotQuality = Literal["eligible", "ineligible"]
ComparisonOutcome = Literal["baseline_created", "no_change", "changed", "unavailable", "rebaseline_required"]
CoverageQuality = Literal["complete", "partial", "insufficient"]
PromotionState = Literal["pending", "promoted", "rejected_stale", "skipped"]
ChangeKind = Literal["added", "removed", "modified"]


class FetchOutcome(Contract):
    """One attempted source fetch; failed/blocked/empty attempts never become baselines."""

    id: UUID
    run_id: UUID
    source_id: UUID
    revision_id: UUID
    requested_url: str = Field(max_length=2048)
    final_url: str | None = Field(None, max_length=2048)
    http_status: int | None = Field(None, ge=100, le=599)
    fetch_status: FetchStatus
    reason_codes: list[str] = Field(default_factory=list, max_length=16)
    bytes_observed: int = Field(ge=0)
    truncated: bool = False
    snapshot_id: UUID | None = None
    attempted_at: datetime
    observed_at: datetime


class SourceSnapshot(Contract):
    """Immutable normalized observation; evidence offsets resolve into normalized text only."""

    id: UUID
    owner_id: str = Field(min_length=1, max_length=36)
    source_id: UUID
    run_id: UUID
    fetch_outcome_id: UUID
    requested_url: str = Field(max_length=2048)
    final_url: str = Field(max_length=2048)
    source_context_hash: str = Field(min_length=64, max_length=64)
    source_config_version: str = Field(min_length=64, max_length=64)
    extractor_version: str = Field(min_length=1, max_length=32)
    normalization_version: str = Field(min_length=1, max_length=32)
    captured_uri: str = Field(max_length=512)
    captured_hash: str = Field(min_length=64, max_length=64)
    normalized_uri: str = Field(max_length=512)
    normalized_hash: str = Field(min_length=64, max_length=64)
    title: str | None = Field(None, max_length=500)
    content_type: str | None = Field(None, max_length=64)
    quality: SnapshotQuality
    quality_reason_codes: list[str] = Field(default_factory=list, max_length=16)
    truncated: bool = False
    fetched_at: datetime
    observed_at: datetime


class RunSourceComparison(Contract):
    """Per-source run verdict against the pinned baseline; promotion is decided separately."""

    run_id: UUID
    source_id: UUID
    baseline_snapshot_id: UUID | None = None
    current_snapshot_id: UUID | None = None
    outcome: ComparisonOutcome
    quality: CoverageQuality
    reason_codes: list[str] = Field(default_factory=list, max_length=16)
    promotion: PromotionState = "pending"
    promoted_at: datetime | None = None
    decided_at: datetime


class ChangeCandidate(Contract):
    """One section-level difference; empty side is valid for added/removed kinds."""

    id: UUID
    run_id: UUID
    source_id: UUID
    before_snapshot_id: UUID
    after_snapshot_id: UUID
    kind: ChangeKind
    section: str | None = Field(None, max_length=500)
    before_start: int | None = Field(None, ge=0)
    before_end: int | None = Field(None, ge=0)
    after_start: int | None = Field(None, ge=0)
    after_end: int | None = Field(None, ge=0)
    before_excerpt: str = Field(default="", max_length=MAX_EXCERPT_CHARS)
    after_excerpt: str = Field(default="", max_length=MAX_EXCERPT_CHARS)
    diff_algorithm_version: str = Field(min_length=1, max_length=32)
    diff_hash: str = Field(min_length=64, max_length=64)
    detected_at: datetime

    @model_validator(mode="after")
    def consistent_sides(self) -> "ChangeCandidate":
        if self.kind == "added" and (self.before_excerpt or not self.after_excerpt):
            raise ValueError("Added candidates require an empty before side and a nonempty after side.")
        if self.kind == "removed" and (not self.before_excerpt or self.after_excerpt):
            raise ValueError("Removed candidates require a nonempty before side and an empty after side.")
        if self.kind == "modified" and (not self.before_excerpt or not self.after_excerpt):
            raise ValueError("Modified candidates require both sides.")
        return self
