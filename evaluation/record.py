"""Typed contract for one AgentFlow evaluation run."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

FailureStage = Literal[
    "api",
    "supervisor",
    "queue",
    "worker",
    "llm",
    "tool",
    "persistence",
    "sse",
    "artifact",
    "other",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitMetadata(StrictModel):
    branch: str = "unknown"
    commit: str = "unknown"


class RunConfiguration(StrictModel):
    mode: Literal["live", "replay"] = "live"
    model: str = Field(min_length=1)
    quantization: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0)
    max_output_tokens: Optional[PositiveInt] = None
    prompt_revision: Optional[str] = None
    tool_revision: Optional[str] = None


class ComponentTiming(StrictModel):
    name: str = Field(min_length=1)
    calls: NonNegativeInt = 1
    total_ms: NonNegativeInt
    max_ms: Optional[NonNegativeInt] = None
    failures: NonNegativeInt = 0


class LLMCallTiming(StrictModel):
    component: str = Field(min_length=1)
    calls: NonNegativeInt = 1
    total_ms: NonNegativeInt
    ttft_ms: Optional[NonNegativeInt] = None
    input_tokens: Optional[NonNegativeInt] = None
    output_tokens: Optional[NonNegativeInt] = None


class ThroughputMetrics(StrictModel):
    concurrency: Optional[NonNegativeInt] = None
    offered_requests_per_second: Optional[NonNegativeFloat] = None
    observed_requests_per_second: Optional[NonNegativeFloat] = None
    test_duration_seconds: Optional[NonNegativeFloat] = None
    accepted_runs: Optional[NonNegativeInt] = None
    completed_runs: Optional[NonNegativeInt] = None
    peak_active_runs: Optional[NonNegativeInt] = None


class EvidenceSource(StrictModel):
    url: AnyHttpUrl
    title: Optional[str] = None
    published_at: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    source_class: Literal["official_primary", "peer_reviewed", "secondary", "other"] = "other"
    outcome: Literal["success", "partial", "failed", "not_checked"] = "not_checked"
    supports_claim: Optional[bool] = None
    note: Optional[str] = None


class PerformanceMetrics(StrictModel):
    api_status_code: Optional[int] = Field(default=None, ge=100, le=599)
    failure_stage: Optional[FailureStage] = None
    api_response_ms: Optional[NonNegativeInt] = None
    supervisor_ttft_ms: Optional[NonNegativeInt] = None
    first_progress_event_ms: Optional[NonNegativeInt] = None
    plan_generation_ms: Optional[NonNegativeInt] = None
    queue_wait_ms: Optional[NonNegativeInt] = None
    workflow_execution_ms: Optional[NonNegativeInt] = None
    end_to_end_ms: Optional[NonNegativeInt] = None
    sse_delivery_lag_ms: Optional[NonNegativeInt] = None
    sse_reconnects: Optional[NonNegativeInt] = None
    node_timings: list[ComponentTiming] = Field(default_factory=list)
    tool_timings: list[ComponentTiming] = Field(default_factory=list)
    llm_calls: list[LLMCallTiming] = Field(default_factory=list)
    load_test: Optional[ThroughputMetrics] = None


class QualityMetrics(StrictModel):
    requested_dimensions: Optional[NonNegativeInt] = None
    covered_dimensions: Optional[NonNegativeInt] = None
    sources_found: Optional[NonNegativeInt] = None
    sources_validated: Optional[NonNegativeInt] = None
    factual_claims: Optional[NonNegativeInt] = None
    claims_with_citations: Optional[NonNegativeInt] = None
    unsupported_claims: Optional[NonNegativeInt] = None
    acceptance_checks_passed: Optional[NonNegativeInt] = None
    acceptance_checks_total: Optional[NonNegativeInt] = None
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)
    human_score_1_to_5: Optional[int] = Field(default=None, ge=1, le=5)
    evaluator_notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_counts(self) -> "QualityMetrics":
        pairs = (
            (self.covered_dimensions, self.requested_dimensions, "covered_dimensions"),
            (self.sources_validated, self.sources_found, "sources_validated"),
            (self.claims_with_citations, self.factual_claims, "claims_with_citations"),
            (self.acceptance_checks_passed, self.acceptance_checks_total, "acceptance_checks_passed"),
        )
        for actual, total, field_name in pairs:
            if (actual is None) != (total is None):
                raise ValueError(f"{field_name} and its total must be recorded together")
            if actual is not None and total is not None and actual > total:
                raise ValueError(f"{field_name} cannot exceed its total")
        return self


class ResourceMetrics(StrictModel):
    cpu_utilization_percent: Optional[NonNegativeFloat] = Field(default=None, le=100)
    peak_ram_mb: Optional[NonNegativeFloat] = None
    gpu_name: Optional[str] = None
    gpu_utilization_percent: Optional[NonNegativeFloat] = Field(default=None, le=100)
    peak_vram_mb: Optional[NonNegativeFloat] = None


class ArtifactReferences(StrictModel):
    report_path: Optional[str] = None
    chart_path: Optional[str] = None
    raw_log_path: Optional[str] = None


class EvaluationRunRecord(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluation_id: str = Field(min_length=1)
    agentflow_run_id: Optional[str] = None
    case_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: Optional[datetime] = None
    git: GitMetadata = Field(default_factory=GitMetadata)
    configuration: RunConfiguration
    execution_status: Literal["in_progress", "completed", "partial", "failed", "cancelled"] = "in_progress"
    evaluation_verdict: Literal["not_evaluated", "pass", "needs_review", "fail"] = "not_evaluated"
    performance: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    quality: QualityMetrics = Field(default_factory=QualityMetrics)
    resources: ResourceMetrics = Field(default_factory=ResourceMetrics)
    artifacts: ArtifactReferences = Field(default_factory=ArtifactReferences)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "EvaluationRunRecord":
        if self.started_at.tzinfo is None:
            raise ValueError("started_at must include a timezone")
        if self.finished_at is not None:
            if self.finished_at.tzinfo is None:
                raise ValueError("finished_at must include a timezone")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at cannot be earlier than started_at")
        if self.execution_status == "in_progress" and self.finished_at is not None:
            raise ValueError("an in-progress run cannot have finished_at")
        if self.execution_status != "in_progress" and self.finished_at is None:
            raise ValueError("a terminal run must have finished_at")
        return self
