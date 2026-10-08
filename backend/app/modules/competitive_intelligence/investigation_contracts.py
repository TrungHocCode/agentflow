"""CI-P4 bounded multi-agent investigation contracts.

The Coordinator is code: it plans rounds, validates scope/budget/DAG shape and records
decisions. An InvestigationRound is an immutable accepted DAG delta plus persisted task
lineage. Round tasks are mechanical assignments, never verified business findings.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.modules.competitive_intelligence.models import Contract

CIRole = Literal["coordinator", "source_researcher", "product_analyst", "evidence_verifier",
                 "competitive_analyst", "report_agent"]
CI_ROLE_TOOLS: dict[str, list[str]] = {
    "source_researcher": ["news_crawler", "news_crawler_batch", "http_request"],
    "product_analyst": ["http_request", "text_summarizer"],
    "evidence_verifier": ["text_summarizer"],
    "competitive_analyst": ["http_request", "text_summarizer"],
    "report_agent": ["markdown_report_generator", "chart_generator"],
}
ROUND_VERSION = "ci-round-v1"
MAX_ROUND_TASKS = 20

RoundStatus = Literal["proposed", "accepted", "rejected", "superseded", "completed", "interrupted"]
CoverageStatus = Literal["covered", "uncovered"]
DecisionOutcome = Literal["accepted", "rejected"]


class InvestigationQuestion(Contract):
    """One business question the run must answer; coverage is tracked per question."""

    id: UUID
    text: str = Field(min_length=1, max_length=1000)
    dimension: str = Field(min_length=1, max_length=64)
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=50)


class RoundTask(Contract):
    """One assignment inside a round; scope references must resolve to approved configuration."""

    task_key: str = Field(min_length=1, max_length=128)
    role: CIRole
    description: str = Field(min_length=1, max_length=2000)
    tool_names: list[str] = Field(default_factory=list, max_length=8)
    source_ids: list[UUID] = Field(default_factory=list, max_length=20)
    snapshot_ids: list[UUID] = Field(default_factory=list, max_length=20)
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=50)
    question_ids: list[UUID] = Field(default_factory=list, max_length=20)
    depends_on_keys: list[str] = Field(default_factory=list, max_length=20)
    max_iterations: int | None = Field(None, ge=1, le=10)
    timeout_seconds: int | None = Field(None, ge=1, le=3600)
    estimated_calls: int = Field(1, ge=1, le=32)


class InvestigationRound(Contract):
    """Accepted DAG delta for one bounded round; immutable once accepted."""

    id: UUID
    run_id: UUID
    watchlist_id: UUID
    revision_id: UUID
    round_number: int = Field(ge=1, le=10)
    parent_round_id: UUID | None = None
    tasks: list[RoundTask] = Field(min_length=1, max_length=MAX_ROUND_TASKS)
    scope_digest: str = Field(min_length=64, max_length=64)
    reserved_calls: int = Field(ge=0)
    reserved_tokens: int = Field(ge=0)
    status: RoundStatus = "proposed"
    decided_at: datetime | None = None
    decided_by: str | None = Field(None, max_length=64)
    rejection_reasons: list[str] = Field(default_factory=list, max_length=16)
    created_at: datetime

    @model_validator(mode="after")
    def consistent_tasks(self) -> "InvestigationRound":
        keys = [task.task_key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("Round task keys must be unique.")
        for task in self.tasks:
            unknown = [key for key in task.depends_on_keys if key not in keys]
            if unknown:
                raise ValueError(f"Round task depends on unknown keys: {unknown}.")
            if task.role == "coordinator":
                raise ValueError("Coordinator plans rounds; it never takes a round task.")
        return self


class RoundDecision(Contract):
    outcome: DecisionOutcome
    reasons: list[str] = Field(default_factory=list, max_length=16)
    decided_at: datetime


class CoverageEntry(Contract):
    question_id: UUID
    status: CoverageStatus
    round_numbers: list[int] = Field(default_factory=list, max_length=10)


class RoundScope(Contract):
    """Frozen approved scope a round is validated against; digests must match run acceptance."""

    approved_source_ids: list[UUID] = Field(max_length=40)
    approved_urls: list[str] = Field(max_length=40)
    config_hash: str = Field(min_length=64, max_length=64)
