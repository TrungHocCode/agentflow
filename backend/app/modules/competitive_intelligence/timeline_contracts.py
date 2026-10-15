"""CI-P9 timeline and history Q&A contracts.

Timeline events are mechanical records with resolvable references. Answers cite
snapshot spans that resolve through owner-scoped retrieval; unknown questions return
not-observed, never invented text.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.competitive_intelligence.models import Contract

TimelineKind = Literal["snapshot_captured", "change_detected", "baseline_promoted", "round_decided",
                       "brief_built"]
AnswerVerdict = Literal["answered", "not_observed"]
QuestionIntent = Literal["temporal", "factual", "summary", "keyword"]


class TimelineEvent(Contract):
    id: UUID
    at: datetime
    kind: TimelineKind
    source_id: UUID | None = None
    summary: str = Field(min_length=1, max_length=1000)
    snapshot_ids: list[UUID] = Field(default_factory=list, max_length=8)
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=8)
    comparison_outcome: str | None = Field(None, max_length=32)
    round_id: UUID | None = None
    brief_id: UUID | None = None


class Citation(Contract):
    snapshot_id: UUID
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    excerpt: str = Field(max_length=2000)


class HistoryAnswer(Contract):
    question: str = Field(min_length=1, max_length=2000)
    intent: QuestionIntent
    verdict: AnswerVerdict
    text: str = Field(max_length=8000)
    citations: list[Citation] = Field(default_factory=list, max_length=20)


class AskRequest(Contract):
    question: str = Field(min_length=1, max_length=2000)
