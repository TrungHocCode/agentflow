"""Persistence contracts for research outputs."""

from pathlib import Path
from typing import List, Protocol

from app.modules.results.models import ArtifactRecord, EvidenceRecord, ResultRecord


class ArtifactStorage(Protocol):
    """Filesystem operations needed by run collection and owned downloads."""

    def is_generated_file(self, source_path: str) -> bool:
        ...

    def ingest_file(
        self, source_path: str, user_id: str, run_id: str,
        task_execution_id: str | None = None, name: str | None = None,
    ) -> ArtifactRecord | None:
        ...

    def resolve(self, storage_uri: str) -> Path:
        ...


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
