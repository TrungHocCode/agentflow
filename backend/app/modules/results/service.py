"""Use cases for reading structured research outputs."""

from typing import List

from app.modules.results.models import ArtifactRecord, EvidenceRecord, ResultRecord
from app.modules.results.ports import ResearchDataRepository


class ResearchResultService:
    """Keep result reads behind a bounded-context application service."""

    def __init__(self, repository: ResearchDataRepository) -> None:
        self.repository = repository

    async def list_results(self, run_id: str, limit: int = 200) -> List[ResultRecord]:
        return await self.repository.list_results(run_id, limit)

    async def list_evidence(self, run_id: str, limit: int = 200) -> List[EvidenceRecord]:
        return await self.repository.list_evidence(run_id, limit)

    async def get_evidence(self, evidence_id: str, run_id: str) -> EvidenceRecord | None:
        return await self.repository.get_evidence(evidence_id, run_id)

    async def list_artifacts(self, run_id: str, limit: int = 200) -> List[ArtifactRecord]:
        return await self.repository.list_artifacts(run_id, limit)

    async def get_artifact(self, artifact_id: str, run_id: str) -> ArtifactRecord | None:
        return await self.repository.get_artifact(artifact_id, run_id)
