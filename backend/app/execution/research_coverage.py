"""Requirement coverage over all accepted claims, never inferred from chunk gaps."""

from app.execution.research_contracts import EvidenceClaim, ResearchRequirement, RequirementCoverage


def reconcile_coverage(
    requirements: list[ResearchRequirement], claims: list[EvidenceClaim],
) -> list[RequirementCoverage]:
    """Exact normalized subject/metric matching; observed is not semantically verified."""
    def normalize(value: str) -> str:
        return " ".join(value.split()).casefold()
    known: dict[str, ResearchRequirement] = {}
    for requirement in requirements:
        previous = known.get(requirement.id)
        if previous is not None and previous != requirement:
            raise ValueError("Conflicting research requirement definitions.")
        known[requirement.id] = requirement
    output = []
    for requirement in known.values():
        subjects = {normalize(value) for value in [requirement.subject, *requirement.subject_aliases]}
        fields = {normalize(value) for value in [requirement.field, *requirement.field_aliases]}
        matches = sorted({claim.evidence_id for claim in claims
                          if normalize(claim.subject) in subjects and normalize(claim.metric or "") in fields})
        output.append(RequirementCoverage(requirement=requirement,
                                         status="observed" if matches else "unresolved", evidence_ids=matches))
    return output
