"""Versioned research contracts; extraction candidates cannot choose source identity."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=800)
    excerpt: str = Field(min_length=1, max_length=800)
    source_span_id: str | None = Field(default=None, max_length=32,
        description="ID of the supporting source paragraph supplied by the backend; not an evidence ID")
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


class ChunkDiagnostic(BaseModel):
    """A chunk observation is not a conclusion about the entire publisher document."""

    document_id: str
    chunk_id: str
    missing_fields: list[str] = Field(default_factory=list)


class ResearchRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    subject_aliases: list[str] = Field(default_factory=list, max_length=8)
    field_aliases: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_nonblank_values(self) -> "ResearchRequirement":
        values = [self.id, self.subject, self.field, *self.subject_aliases, *self.field_aliases]
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Research requirements and aliases must be bounded and nonblank.")
        return self


class RequirementCoverage(BaseModel):
    requirement: ResearchRequirement
    status: Literal["observed", "unresolved"]
    evidence_ids: list[str] = Field(default_factory=list)


class SourceOutcome(BaseModel):
    source_url: str
    document_id: str | None = None
    status: Literal["fetch_failed", "empty", "invalid_extraction", "processed", "unprocessed"]
    code: str | None = None


class AnalysisLimitation(BaseModel):
    origin: Literal["extraction", "coverage", "reconciliation"]
    message: str
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    schema_version: str = "1"
    claims: list[EvidenceClaim] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    processed_chunks: int = 0
    failed_chunks: int = 0
    unprocessed_chunks: int = 0
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    chunk_diagnostics: list[ChunkDiagnostic] = Field(default_factory=list)
    coverage_scope: Literal["extraction_only"] = "extraction_only"
    requirements: list[ResearchRequirement] = Field(default_factory=list, max_length=64)
    requirement_coverage: list[RequirementCoverage] = Field(default_factory=list)
    source_outcomes: list[SourceOutcome] = Field(default_factory=list)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    status: Literal["complete", "partial", "failed"] = "partial"


class SynthesisFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)
    verification: Literal["unverified"] = "unverified"


class SynthesisResult(BaseModel):
    schema_version: str = "1"
    findings: list[SynthesisFinding] = Field(default_factory=list, max_length=16)
    conflicts: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(default_factory=list, max_length=16)


class EvidenceAnalysis(SynthesisResult):
    """Typed handoff; validation labels remain quotation-level, not semantic verification."""

    evidence: list[EvidenceClaim] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    input_claim_count: int = 0
    requirement_coverage: list[RequirementCoverage] = Field(default_factory=list)
    source_outcomes: list[SourceOutcome] = Field(default_factory=list)
    analysis_limitations: list[AnalysisLimitation] = Field(default_factory=list)
    coverage_scope: Literal["extraction_only"] = "extraction_only"
    chunk_diagnostics: list[ChunkDiagnostic] = Field(default_factory=list)
    status: Literal["done", "partial"] = "partial"
