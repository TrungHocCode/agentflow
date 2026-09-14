"""Persistence contracts for research outputs."""

from typing import List, Protocol

from app.modules.results.models import ArtifactRecord, EvidenceRecord, ResultRecord


class ResearchDataRepository(Protocol):
    async def save_result(self, result: ResultRecord) -> ResultRecord:
        ...

    async def list_results(self, run_id: str, limit: int = 200) -> List[ResultRecord]:
        ...

    async def save_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        ...

    async def list_evidence(self, run_id: str, limit: int = 200) -> List[EvidenceRecord]:
        ...

    async def get_evidence(self, evidence_id: str, run_id: str) -> EvidenceRecord | None:
        ...

    async def save_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        ...

    async def list_artifacts(self, run_id: str, limit: int = 200) -> List[ArtifactRecord]:
        ...

    async def get_artifact(self, artifact_id: str, run_id: str) -> ArtifactRecord | None:
        ...
