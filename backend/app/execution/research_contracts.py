"""Versioned research contracts; extraction candidates cannot choose source identity."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=600)
    excerpt: str = Field(min_length=1, max_length=800)
    subject: str = Field(default="", max_length=200)
    metric: str | None = Field(default=None, max_length=200, description="Benchmark or measurement name, not the score")
    value_text: str | None = Field(default=None, max_length=100, description="Exact numeric string without its unit")
    unit: str | None = Field(default=None, max_length=50)
    evaluation_setup: str | None = Field(default=None, max_length=300)


class ChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=8)
    missing_fields: list[str] = Field(default_factory=list, max_length=12)


class EvidenceClaim(ExtractedClaim):
    evidence_id: str
    document_id: str
    chunk_id: str
    source_url: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    validation: Literal["quote_matched"] = "quote_matched"


class SourceDocument(BaseModel):
    schema_version: str = "1"
    document_id: str
    run_id: str
    task_id: str
    source_url: str
    content_hash: str
    text: str
    truncated_upstream: bool = False


class ResearchResult(BaseModel):
    schema_version: str = "1"
    claims: list[EvidenceClaim] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    processed_chunks: int = 0
    failed_chunks: int = 0
    unprocessed_chunks: int = 0
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "failed"] = "partial"


class SynthesisFinding(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)


class SynthesisResult(BaseModel):
    schema_version: str = "1"
    findings: list[SynthesisFinding] = Field(default_factory=list, max_length=16)
    conflicts: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(default_factory=list, max_length=16)
