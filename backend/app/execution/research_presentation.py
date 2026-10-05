"""Deterministic presentation of typed evidence; no final inference rewrite."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.execution.research_contracts import (
    EvidenceClaim, RequirementCoverage, SourceOutcome, SynthesisFinding,
)
from app.execution.research_validation import supported_literals, supported_numbers
from app.execution.research_coverage import reconcile_coverage


class EvidenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    findings: list[SynthesisFinding] = Field(min_length=1, max_length=64)
    evidence: list[EvidenceClaim] = Field(min_length=1, max_length=512)
    requirement_coverage: list[RequirementCoverage] = Field(default_factory=list)
    source_outcomes: list[SourceOutcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lineage(self) -> "EvidenceReport":
        claims = {claim.evidence_id: claim for claim in self.evidence}
        if len(claims) != len(self.evidence):
            raise ValueError("Report evidence IDs must be unique.")
        for claim in self.evidence:
            url = urlparse(claim.source_url)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("Report citation must reference an HTTP source.")
            if claim.value_text and claim.value_text not in claim.excerpt:
                raise ValueError("Report value does not retain its exact source spelling.")
            for value in (claim.metric, claim.unit, claim.evaluation_setup):
                if value and value.casefold() not in claim.excerpt.casefold():
                    raise ValueError("Report measurement qualifier is not in its source excerpt.")
        for finding in self.findings:
            if not set(finding.evidence_ids).issubset(claims):
                raise ValueError("Report finding references unknown evidence.")
            supporting = [claims[identity] for identity in finding.evidence_ids]
            text = finding.text
            for claim in supporting:
                if claim.subject:
                    text = text.replace(claim.subject, "")
            excerpts = " ".join(claim.excerpt for claim in supporting)
            if not supported_numbers(text, excerpts) or not supported_literals(finding.text, excerpts):
                raise ValueError("Report finding changed supported numbers or literals.")
        for coverage in self.requirement_coverage:
            if not set(coverage.evidence_ids).issubset(claims):
                raise ValueError("Report coverage references unknown evidence.")
            if (coverage.status == "observed") != bool(coverage.evidence_ids):
                raise ValueError("Coverage status contradicts its evidence references.")
        expected = reconcile_coverage([row.requirement for row in self.requirement_coverage], self.evidence)
        if expected != self.requirement_coverage:
            raise ValueError("Report coverage is inconsistent with the accepted claim matrix.")
        return self


def _cell(value: str) -> str:
    value = value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    # Render source text as literal data, not executable/raw HTML or Markdown links.
    fence = "`" * (max((len(match.group()) for match in re.finditer(r"`+", value)), default=0) + 1)
    return f"{fence} {value} {fence}"


def render_evidence_report(report: EvidenceReport) -> str:
    """Facts and coverage retain exact source wording and explicit uncertainty labels."""
    claims = {claim.evidence_id: claim for claim in report.evidence}
    lines = [f"# {report.title}", "", "## Findings", "",
             "Analysis below is evidence-linked, not semantically verified.", ""]
    for finding in report.findings:
        links = " ".join(f"[Evidence {index + 1}]({claims[identity].source_url})"
                         for index, identity in enumerate(finding.evidence_ids))
        safe_text = re.sub(r"</?[A-Za-z_][^>]*>", lambda match: f"`{match.group()}`", finding.text)
        lines.extend([f"- {safe_text} {links}", ""])
    lines.extend(["## Source-backed observations", "",
                  "| Subject | Field | Exact value | Unit | Evaluation conditions | Source excerpt | Source |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
    for claim in report.evidence:
        values = [claim.subject, claim.metric or "Unspecified", claim.value_text or "Unspecified",
                  claim.unit or "Unspecified", claim.evaluation_setup or "Unspecified", claim.excerpt]
        lines.append("| " + " | ".join(_cell(value) for value in values)
                     + f" | [Source]({claim.source_url}) |")
    lines.extend(["", "## Coverage and limitations", "",
                  "Observed means an extracted subject/field match, not verification or complete publisher coverage.",
                  "Unresolved means evidence is insufficient; "
                  "it does not mean the publisher omitted the information.", ""])
    if report.requirement_coverage:
        lines.extend(["| Requested subject | Requested field | Extraction coverage |",
                      "| --- | --- | --- |"])
        for item in report.requirement_coverage:
            lines.append(f"| {_cell(item.requirement.subject)} | {_cell(item.requirement.field)} | {item.status} |")
    else:
        lines.append("No explicit requirement matrix was supplied; request-level completeness is not established.")
    for outcome in report.source_outcomes:
        if outcome.status != "processed":
            lines.append(f"- Source processing: {outcome.status}; {outcome.source_url}")
    lines.extend(["", "## Evidence lineage", ""])
    for claim in report.evidence:
        lines.append(f"- `{claim.evidence_id}` → document `{claim.document_id}`, chunk `{claim.chunk_id}`, "
                     f"offsets {claim.start_offset}:{claim.end_offset}; validation: quote_matched.")
    return "\n".join(lines) + "\n"
