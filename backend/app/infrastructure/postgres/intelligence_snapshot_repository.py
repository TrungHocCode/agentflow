"""Transactional CI snapshot capture, comparison history and CAS baseline promotion."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Literal
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import TrackedSourceModel, WatchlistRevisionModel
from app.infrastructure.postgres.models.competitive_intelligence_snapshots import (
    ChangeCandidateModel, FetchOutcomeModel, RunComparisonModel, SourceBaselineModel, SourceSnapshotModel,
)
from app.modules.competitive_intelligence.snapshot_contracts import (
    ChangeCandidate, FetchOutcome, RunSourceComparison, SourceSnapshot,
)
from app.shared.errors import ApplicationError, PersistenceError, ResourceNotFoundError, ValidationError


class PostgresSnapshotRepository:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
                 blobs=None) -> None:
        from app.modules.competitive_intelligence.ports import SnapshotBlobStore
        self.session_factory = session_factory
        self.blobs: SnapshotBlobStore | None = blobs

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except ApplicationError:
                await session.rollback()
                raise
            except Exception as exc:
                await session.rollback()
                raise PersistenceError("Could not persist Competitive Intelligence snapshots.") from exc

    @staticmethod
    async def owned_source(session: AsyncSession, source_id: str, owner_id: str) -> TrackedSourceModel:
        source = (await session.execute(select(TrackedSourceModel).where(
            TrackedSourceModel.id == source_id, TrackedSourceModel.owner_id == owner_id,
            TrackedSourceModel.archived.is_(False)))).scalar_one_or_none()
        if source is None:
            raise ResourceNotFoundError("Tracked source was not found.", entity="source")
        return source

    @staticmethod
    def snapshot_contract(record: SourceSnapshotModel) -> SourceSnapshot:
        return SourceSnapshot(id=UUID(record.id), owner_id=record.owner_id, source_id=UUID(record.source_id),
            run_id=UUID(record.run_id), fetch_outcome_id=UUID(record.fetch_outcome_id),
            requested_url=record.requested_url, final_url=record.final_url,
            source_context_hash=record.source_context_hash, source_config_version=record.source_config_version,
            extractor_version=record.extractor_version, normalization_version=record.normalization_version,
            captured_uri=record.captured_uri, captured_hash=record.captured_hash,
            normalized_uri=record.normalized_uri, normalized_hash=record.normalized_hash, title=record.title,
            content_type=record.content_type, quality=record.quality,
            quality_reason_codes=list(record.quality_reason_codes or []), truncated=record.truncated,
            fetched_at=record.fetched_at, observed_at=record.observed_at)

    async def record_outcome(self, outcome: FetchOutcome, owner_id: str) -> FetchOutcome:
        """Persist a failed/blocked/empty attempt; such outcomes never reference a snapshot."""
        if outcome.fetch_status == "success":
            raise ValidationError("Successful attempts must be captured with their snapshot.")
        async with self.session() as session:
            source = await self.owned_source(session, str(outcome.source_id), owner_id)
            revision = await session.get(WatchlistRevisionModel, str(outcome.revision_id))
            if revision is None or revision.watchlist_id != source.watchlist_id:
                raise ResourceNotFoundError("Watchlist revision was not found.", entity="revision")
            session.add(FetchOutcomeModel(id=str(outcome.id), run_id=str(outcome.run_id),
                source_id=str(outcome.source_id), revision_id=str(outcome.revision_id), owner_id=owner_id,
                requested_url=outcome.requested_url, final_url=outcome.final_url, http_status=outcome.http_status,
                fetch_status=outcome.fetch_status, reason_codes=list(outcome.reason_codes),
                bytes_observed=outcome.bytes_observed, truncated=outcome.truncated, snapshot_id=None,
                attempted_at=outcome.attempted_at, observed_at=outcome.observed_at))
            await session.flush()
            return outcome

    async def capture_snapshot(self, outcome: FetchOutcome, snapshot: SourceSnapshot, captured_text: str,
                               normalized_text: str) -> SourceSnapshot:
        if self.blobs is None:
            raise ValidationError("Snapshot blob storage is unavailable.")
        async with self.session() as session:
            source = await self.owned_source(session, str(snapshot.source_id), snapshot.owner_id)
            revision = await session.get(WatchlistRevisionModel, str(outcome.revision_id))
            if revision is None or revision.watchlist_id != source.watchlist_id:
                raise ResourceNotFoundError("Watchlist revision was not found.", entity="revision")
            captured_uri = self.blobs.put(snapshot.owner_id, str(snapshot.run_id), str(snapshot.id),
                                          "captured", captured_text)
            normalized_uri = self.blobs.put(snapshot.owner_id, str(snapshot.run_id), str(snapshot.id),
                                            "normalized", normalized_text)
            outcome_row = FetchOutcomeModel(id=str(outcome.id), run_id=str(outcome.run_id),
                source_id=str(outcome.source_id), revision_id=str(outcome.revision_id),
                owner_id=snapshot.owner_id, requested_url=outcome.requested_url, final_url=outcome.final_url,
                http_status=outcome.http_status, fetch_status=outcome.fetch_status,
                reason_codes=list(outcome.reason_codes), bytes_observed=outcome.bytes_observed,
                truncated=outcome.truncated, snapshot_id=None, attempted_at=outcome.attempted_at,
                observed_at=outcome.observed_at)
            session.add(outcome_row)
            await session.flush()
            session.add(SourceSnapshotModel(id=str(snapshot.id), owner_id=snapshot.owner_id,
                source_id=str(snapshot.source_id), run_id=str(snapshot.run_id),
                fetch_outcome_id=str(snapshot.fetch_outcome_id), requested_url=snapshot.requested_url,
                final_url=snapshot.final_url, source_context_hash=snapshot.source_context_hash,
                source_config_version=snapshot.source_config_version,
                extractor_version=snapshot.extractor_version,
                normalization_version=snapshot.normalization_version, captured_uri=captured_uri,
                captured_hash=snapshot.captured_hash, normalized_uri=normalized_uri,
                normalized_hash=snapshot.normalized_hash, title=snapshot.title,
                content_type=snapshot.content_type, quality=snapshot.quality,
                quality_reason_codes=list(snapshot.quality_reason_codes), truncated=snapshot.truncated,
                fetched_at=snapshot.fetched_at, observed_at=snapshot.observed_at))
            await session.flush()
            outcome_row.snapshot_id = str(snapshot.id)
            await session.flush()
            return snapshot.model_copy(update={"captured_uri": captured_uri, "normalized_uri": normalized_uri})

    async def get_snapshot(self, snapshot_id: str, owner_id: str) -> SourceSnapshot:
        async with self.session() as session:
            record = await session.get(SourceSnapshotModel, snapshot_id)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Snapshot was not found.", entity="snapshot")
            return self.snapshot_contract(record)

    async def snapshot_text(self, snapshot_id: str, owner_id: str) -> tuple[str, str]:
        if self.blobs is None:
            raise ValidationError("Snapshot blob storage is unavailable.")
        snapshot = await self.get_snapshot(snapshot_id, owner_id)
        return (self.blobs.read_full(snapshot.captured_uri), self.blobs.read_full(snapshot.normalized_uri))

    async def read_snapshot_span(self, snapshot_id: str, owner_id: str, representation: str, start: int,
                                 limit: int) -> str:
        if representation not in ("captured", "normalized"):
            raise ValidationError("Snapshot representation must be captured or normalized.")
        if self.blobs is None:
            raise ValidationError("Snapshot blob storage is unavailable.")
        snapshot = await self.get_snapshot(snapshot_id, owner_id)
        uri = snapshot.captured_uri if representation == "captured" else snapshot.normalized_uri
        return self.blobs.read_span(uri, start, limit)

    async def latest_baseline(self, source_id: str, owner_id: str) -> SourceSnapshot | None:
        async with self.session() as session:
            await self.owned_source(session, source_id, owner_id)
            pointer = (await session.execute(select(SourceBaselineModel).where(
                SourceBaselineModel.source_id == source_id, SourceBaselineModel.owner_id == owner_id
                ).order_by(desc(SourceBaselineModel.promoted_at)).limit(1))).scalar_one_or_none()
            if pointer is None:
                return None
            return self.snapshot_contract(await session.get(SourceSnapshotModel, pointer.snapshot_id))

    async def record_comparison(self, comparison: RunSourceComparison, owner_id: str,
                                candidates: list[ChangeCandidate]) -> RunSourceComparison:
        async with self.session() as session:
            await self.owned_source(session, str(comparison.source_id), owner_id)
            session.add(RunComparisonModel(run_id=str(comparison.run_id), source_id=str(comparison.source_id),
                owner_id=owner_id,
                baseline_snapshot_id=str(comparison.baseline_snapshot_id)
                if comparison.baseline_snapshot_id else None,
                current_snapshot_id=str(comparison.current_snapshot_id)
                if comparison.current_snapshot_id else None, outcome=comparison.outcome,
                quality=comparison.quality, reason_codes=list(comparison.reason_codes),
                promotion=comparison.promotion, promoted_at=comparison.promoted_at,
                decided_at=comparison.decided_at))
            for candidate in candidates:
                session.add(ChangeCandidateModel(id=str(candidate.id), run_id=str(candidate.run_id),
                    source_id=str(candidate.source_id), owner_id=owner_id,
                    before_snapshot_id=str(candidate.before_snapshot_id),
                    after_snapshot_id=str(candidate.after_snapshot_id), kind=candidate.kind,
                    section=candidate.section, before_start=candidate.before_start,
                    before_end=candidate.before_end, after_start=candidate.after_start,
                    after_end=candidate.after_end, before_excerpt=candidate.before_excerpt,
                    after_excerpt=candidate.after_excerpt,
                    diff_algorithm_version=candidate.diff_algorithm_version, diff_hash=candidate.diff_hash,
                    detected_at=candidate.detected_at))
            await session.flush()
            return comparison

    async def promote_baseline(self, snapshot: SourceSnapshot, run_id: str,
                               expected_snapshot_id: str | None) -> Literal["promoted", "rejected_stale"]:
        if snapshot.quality != "eligible" or snapshot.truncated:
            raise ValidationError("Only eligible non-truncated snapshots may become baselines.")
        async with self.session() as session:
            await self.owned_source(session, str(snapshot.source_id), snapshot.owner_id)
            pointer = (await session.execute(select(SourceBaselineModel).where(
                SourceBaselineModel.source_id == str(snapshot.source_id),
                SourceBaselineModel.comparison_context_hash == snapshot.source_context_hash,
                SourceBaselineModel.normalization_version == snapshot.normalization_version,
                ).with_for_update())).scalar_one_or_none()
            current = pointer.snapshot_id if pointer is not None else None
            if current != expected_snapshot_id:
                return "rejected_stale"
            if pointer is None:
                session.add(SourceBaselineModel(source_id=str(snapshot.source_id),
                    comparison_context_hash=snapshot.source_context_hash,
                    normalization_version=snapshot.normalization_version, owner_id=snapshot.owner_id,
                    snapshot_id=str(snapshot.id), run_id=run_id, promoted_at=snapshot.observed_at))
            else:
                pointer.snapshot_id, pointer.run_id = str(snapshot.id), run_id
                pointer.promoted_at = snapshot.observed_at
            await session.flush()
            return "promoted"
