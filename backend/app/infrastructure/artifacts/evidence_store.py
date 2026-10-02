"""Owned raw-source storage with durable PostgreSQL metadata and exact excerpts."""

import asyncio
import hashlib
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from app.execution.research_contracts import ResearchResult, SourceDocument
from app.modules.results.models import EvidenceRecord, ResultRecord
from app.modules.results.ports import ResearchDataRepository
from app.modules.runs.ports import RunRepository


class DurableEvidenceStore:
    def __init__(self, repository: ResearchDataRepository, root: str, run_repository: RunRepository) -> None:
        self.repository = repository
        self.root = Path(root).resolve()
        self.run_repository = run_repository

    async def check_active(self, run_id: str) -> None:
        run = await self.run_repository.get(run_id)
        if run is None or run.status not in {"running", "pending", "queued"}:
            raise asyncio.CancelledError("Run is no longer active.")

    async def save_document(self, document: SourceDocument) -> None:
        run = await self.run_repository.get(document.run_id)
        if run is None:
            raise ValueError("Source document has no owning run.")
        run_id = str(UUID(document.run_id))
        owner = hashlib.sha256(run.user_id.encode()).hexdigest()
        filename = hashlib.sha256(document.document_id.encode()).hexdigest() + ".txt"
        directory = (self.root / "sources" / owner / run_id).resolve()
        if self.root not in directory.parents:
            raise ValueError("Source storage escapes the artifact root.")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        if target.is_symlink() or self.root not in target.resolve().parents:
            raise ValueError("Source file escapes the artifact root.")
        if target.exists() and target.read_text(encoding="utf-8") != document.text:
            raise ValueError("Immutable source identity already has different content.")
        if not target.exists():
            target.write_text(document.text, encoding="utf-8")
        await self.repository.save_result(ResultRecord(
            id=str(uuid5(NAMESPACE_URL, f"{document.run_id}:document:{document.document_id}")),
            run_id=document.run_id, task_id=document.task_id, result_type="raw_data",
            content={**document.model_dump(exclude={"text"}),
                     "storage_uri": target.relative_to(self.root).as_posix()},
            metadata={"kind": "source_document", "internal": True},
        ))

    async def save_bundle(self, run_id: str, task_id: str, bundle: ResearchResult) -> None:
        await self.repository.save_result(ResultRecord(
            id=str(uuid5(NAMESPACE_URL, f"{run_id}:evidence_bundle:{task_id}")),
            run_id=run_id, task_id=task_id, result_type="normalized_data",
            content=bundle.model_dump(mode="json"), metadata={"kind": "evidence_bundle"},
        ))
        for claim in bundle.claims:
            await self.repository.save_evidence(EvidenceRecord(
                id=str(uuid5(NAMESPACE_URL, f"{run_id}:{claim.evidence_id}")), run_id=run_id,
                task_execution_id=task_id, source_url=claim.source_url, excerpt=claim.excerpt,
                metadata={"kind": "extracted_claim", **claim.model_dump()},
            ))
