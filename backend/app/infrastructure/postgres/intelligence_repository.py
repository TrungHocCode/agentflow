"""Transactional CI configuration/admission adapter. No development-data fallback."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Callable
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import (
    FlowModel, RunModel, WorkflowVersionModel, WatchlistModel, WatchlistRevisionModel,
    TrackedProductModel, TrackedSourceModel, ProductProfileVersionModel, EvidenceModel,
)
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.modules.competitive_intelligence.models import (
    CreateWatchlist, ProductProfileVersion, Revision, UpdateWatchlist, Watchlist, WatchlistConfig, digest,
)
from app.modules.runs.models import RunDocument
from app.shared.errors import ApplicationError, ConflictError, PersistenceError, ResourceNotFoundError, ValidationError

ACTIVE = ("pending", "created", "waiting_for_approval", "queued", "running", "paused")


class PostgresIntelligenceRepository:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

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
                raise PersistenceError("Could not persist Competitive Intelligence configuration.") from exc

    @staticmethod
    async def owned(session: AsyncSession, watchlist_id: str, owner_id: str,
                    lock: bool = False) -> WatchlistModel:
        query = select(WatchlistModel).where(WatchlistModel.id == watchlist_id, WatchlistModel.owner_id == owner_id)
        if lock:
            query = query.with_for_update()
        record = (await session.execute(query)).scalar_one_or_none()
        if record is None:
            raise ResourceNotFoundError("Watchlist was not found.", entity="watchlist")
        return record

    @staticmethod
    def revision(record: WatchlistRevisionModel) -> Revision:
        return Revision(id=record.id, watchlist_id=record.watchlist_id, revision_number=record.revision_number,
                        config=record.config, config_hash=record.config_hash, approval_status=record.approval_status,
                        approved_at=record.approved_at, approved_by=record.approved_by, created_at=record.created_at)

    async def view(self, session: AsyncSession, record: WatchlistModel) -> Watchlist:
        # Server-side onupdate expires timestamps after flush; load them asynchronously.
        await session.refresh(record)
        revision = await session.get(WatchlistRevisionModel, record.current_revision_id)
        return Watchlist(id=record.id, owner_id=record.owner_id, name=record.name, description=record.description,
                        status=record.status, current_revision=self.revision(revision),
                        created_at=record.created_at, updated_at=record.updated_at)

    async def create(self, request: CreateWatchlist, owner_id: str) -> Watchlist:
        async with self.session() as session:
            record = WatchlistModel(id=str(uuid4()), owner_id=owner_id, name=request.name,
                                    description=request.description, status="active")
            session.add(record)
            await session.flush()
            await self.new_revision(session, record, request.config, 1)
            await session.flush()
            return await self.view(session, record)

    async def get(self, watchlist_id: str, owner_id: str) -> Watchlist:
        async with self.session() as session:
            return await self.view(session, await self.owned(session, watchlist_id, owner_id))

    async def list(self, owner_id: str, limit: int, offset: int) -> list[Watchlist]:
        async with self.session() as session:
            rows = (await session.execute(select(WatchlistModel).where(WatchlistModel.owner_id == owner_id)
                    .order_by(WatchlistModel.updated_at.desc(), WatchlistModel.id).limit(limit).offset(offset))).scalars()
            return [await self.view(session, row) for row in rows]

    async def count(self, owner_id: str, watchlist_id: str | None = None) -> int:
        async with self.session() as session:
            if watchlist_id is None:
                query = select(func.count()).select_from(WatchlistModel).where(WatchlistModel.owner_id == owner_id)
            else:
                await self.owned(session, watchlist_id, owner_id)
                query = select(func.count()).select_from(RunModel).where(
                    RunModel.user_id == owner_id, RunModel.watchlist_id == watchlist_id)
            return (await session.execute(query)).scalar_one()

    async def update(self, watchlist_id: str, owner_id: str, request: UpdateWatchlist) -> Watchlist:
        async with self.session() as session:
            record = await self.owned(session, watchlist_id, owner_id, True)
            self.editable(record, str(request.expected_revision_id))
            previous = await session.get(WatchlistRevisionModel, record.current_revision_id)
            await self.new_revision(session, record, request.config, previous.revision_number + 1)
            record.name, record.description = request.name, request.description
            record.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return await self.view(session, record)

    @staticmethod
    def editable(record: WatchlistModel, revision_id: str) -> None:
        if record.status != "active":
            raise ConflictError("Archived watchlists cannot be edited or run.", code="watchlist_archived")
        if record.current_revision_id != revision_id:
            raise ConflictError("The current watchlist revision has changed.", code="stale_watchlist_revision")

    async def new_revision(self, session: AsyncSession, record: WatchlistModel,
                           config: WatchlistConfig, number: int) -> None:
        config = config.model_copy(deep=True)
        retained_products, retained_sources = set(), set()
        for product in config.products:
            if product.id:
                row = await session.get(TrackedProductModel, str(product.id))
                if row is None or row.watchlist_id != record.id or row.owner_id != record.owner_id:
                    raise ResourceNotFoundError("Linked product was not found.", entity="product")
                if row.kind != product.kind:
                    raise ValidationError("Product kind is immutable; create a new identity instead.")
            else:
                product.id = uuid4()
                row = TrackedProductModel(id=str(product.id), watchlist_id=record.id,
                                          owner_id=record.owner_id, kind=product.kind, archived=False)
                session.add(row)
                await session.flush()
            row.archived = False
            retained_products.add(row.id)
            await self.check_evidence(session, product.profile, record.owner_id)
            if product.profile_version_id:
                profile = await session.get(ProductProfileVersionModel, str(product.profile_version_id))
                if profile is None or profile.product_id != row.id or profile.profile_hash != digest(product.profile):
                    raise ValidationError("Profile version must belong to the product and match its immutable content.")
            else:
                latest = (await session.execute(select(ProductProfileVersionModel)
                          .where(ProductProfileVersionModel.product_id == row.id)
                          .order_by(ProductProfileVersionModel.version_number.desc()).limit(1))).scalar_one_or_none()
                if latest and latest.profile_hash == digest(product.profile):
                    profile = latest
                else:
                    profile = ProductProfileVersionModel(id=str(uuid4()), product_id=row.id,
                        version_number=latest.version_number + 1 if latest else 1,
                        profile=product.profile.model_dump(mode="json"), profile_hash=digest(product.profile))
                    session.add(profile)
                    await session.flush()
                product.profile_version_id = profile.id
            for source in product.sources:
                if source.id:
                    source_row = await session.get(TrackedSourceModel, str(source.id))
                    if (source_row is None or source_row.product_id != row.id
                            or source_row.watchlist_id != record.id or source_row.owner_id != record.owner_id):
                        raise ResourceNotFoundError("Linked source was not found.", entity="source")
                else:
                    source.id = uuid4()
                    source_row = TrackedSourceModel(id=str(source.id), product_id=row.id,
                        watchlist_id=record.id, owner_id=record.owner_id, archived=False,
                        config_version=digest(source))
                    session.add(source_row)
                source_row.archived, source_row.config_version = False, digest(source)
                retained_sources.add(str(source.id))
        for model, retained in ((TrackedProductModel, retained_products), (TrackedSourceModel, retained_sources)):
            rows = (await session.execute(select(model).where(model.watchlist_id == record.id))).scalars()
            for row in rows:
                row.archived = row.id not in retained
        revision = WatchlistRevisionModel(id=str(uuid4()), watchlist_id=record.id, revision_number=number,
            config=config.model_dump(mode="json"), config_hash=digest(config), approval_status="unapproved")
        session.add(revision)
        await session.flush()
        record.current_revision_id = revision.id

    @staticmethod
    async def check_evidence(session: AsyncSession, profile: object, owner_id: str) -> None:
        values = profile.model_dump(mode="json")
        evidence = {identity for fact in values["facts"] for identity in fact["evidence_ids"]}
        for field in ("launch_at", "published_at", "effective_at", "observed_at", "fetched_at"):
            evidence.update((values[field] or {}).get("evidence_ids", []))
        if evidence:
            owned_ids = set((await session.execute(select(EvidenceModel.id).join(RunModel,
                RunModel.run_id == EvidenceModel.run_id).where(RunModel.user_id == owner_id,
                EvidenceModel.id.in_(evidence)))).scalars())
            if owned_ids != evidence:
                raise ResourceNotFoundError("Linked evidence was not found.", entity="evidence")

    async def revisions(self, watchlist_id: str, owner_id: str) -> list[Revision]:
        async with self.session() as session:
            await self.owned(session, watchlist_id, owner_id)
            rows = (await session.execute(select(WatchlistRevisionModel)
                    .where(WatchlistRevisionModel.watchlist_id == watchlist_id)
                    .order_by(WatchlistRevisionModel.revision_number))).scalars()
            return [self.revision(row) for row in rows]

    async def profiles(self, watchlist_id: str, product_id: str, owner_id: str) -> list[ProductProfileVersion]:
        async with self.session() as session:
            await self.owned(session, watchlist_id, owner_id)
            product = await session.get(TrackedProductModel, product_id)
            if product is None or product.owner_id != owner_id or product.watchlist_id != watchlist_id:
                raise ResourceNotFoundError("Product was not found.", entity="product")
            rows = (await session.execute(select(ProductProfileVersionModel)
                .where(ProductProfileVersionModel.product_id == product_id)
                .order_by(ProductProfileVersionModel.version_number))).scalars()
            return [ProductProfileVersion(id=row.id, product_id=row.product_id, version_number=row.version_number,
                        profile=row.profile, profile_hash=row.profile_hash, created_at=row.created_at) for row in rows]

    @staticmethod
    async def published(session: AsyncSession, version_id: str, owner_id: str) -> WorkflowVersionModel:
        row = (await session.execute(select(WorkflowVersionModel).join(FlowModel,
            FlowModel.id == WorkflowVersionModel.workflow_id).where(WorkflowVersionModel.id == version_id,
            WorkflowVersionModel.status == "published", FlowModel.status == "active", FlowModel.user_id == owner_id)
            .with_for_update())).scalar_one_or_none()
        if row is None:
            raise ConflictError("An owned active published workflow version is required.",
                                code="ci_workflow_not_published")
        return row

    async def approve(self, watchlist_id: str, revision_id: str, owner_id: str, version_id: str) -> Revision:
        async with self.session() as session:
            record = await self.owned(session, watchlist_id, owner_id, True)
            self.editable(record, revision_id)
            revision = await session.get(WatchlistRevisionModel, revision_id)
            if revision.config.get("workflow_version_id") != version_id:
                raise ConflictError("Approval must match the workflow pinned in this revision.",
                                    code="ci_workflow_mismatch")
            await self.published(session, version_id, owner_id)
            if revision.approval_status != "approved":
                revision.approval_status, revision.approved_by = "approved", owner_id
                revision.approved_at = datetime.now(timezone.utc)
            await session.flush()
            return self.revision(revision)

    async def workflow_id(self, version_id: str, owner_id: str) -> str:
        async with self.session() as session:
            return (await self.published(session, version_id, owner_id)).workflow_id

    async def archive(self, watchlist_id: str, owner_id: str) -> None:
        async with self.session() as session:
            record = await self.owned(session, watchlist_id, owner_id, True)
            if await self.active_run(session, watchlist_id):
                raise ConflictError("Cancel the active run before archiving.", code="watchlist_run_active")
            record.status = "archived"

    @staticmethod
    async def active_run(session: AsyncSession, watchlist_id: str) -> str | None:
        return (await session.execute(select(RunModel.run_id).where(
            RunModel.watchlist_id == watchlist_id, RunModel.status.in_(ACTIVE)).limit(1))).scalar_one_or_none()

    async def admit_run(self, document: RunDocument, watchlist_id: str, revision_id: str) -> RunDocument:
        try:
            return await self._admit_run(document, watchlist_id, revision_id)
        except PersistenceError:
            if document.idempotency_key:
                async with self.session() as session:
                    existing = (await session.execute(select(RunModel).where(
                        RunModel.user_id == document.user_id, RunModel.idempotency_key == document.idempotency_key)
                        )).scalar_one_or_none()
                    if existing:
                        if existing.idempotency_fingerprint == document.idempotency_fingerprint:
                            return PostgresRunRepository._to_domain(existing)
                        raise ConflictError("Idempotency key was used for a different request.",
                                            code="idempotency_key_reused")
            raise

    async def _admit_run(self, document: RunDocument, watchlist_id: str, revision_id: str) -> RunDocument:
        async with self.session() as session:
            record = await self.owned(session, watchlist_id, document.user_id, True)
            if document.idempotency_key:
                existing = (await session.execute(select(RunModel).where(
                    RunModel.user_id == document.user_id, RunModel.idempotency_key == document.idempotency_key))
                    ).scalar_one_or_none()
                if existing:
                    if existing.idempotency_fingerprint != document.idempotency_fingerprint:
                        raise ConflictError("Idempotency key was used for a different request.",
                                            code="idempotency_key_reused")
                    return PostgresRunRepository._to_domain(existing)
            self.editable(record, revision_id)
            revision = await session.get(WatchlistRevisionModel, revision_id)
            frozen = document.input_data.get("competitive_intelligence", {})
            if (revision.approval_status != "approved" or revision.config_hash != frozen.get("config_hash")
                    or revision.config.get("workflow_version_id") != document.workflow_version_id):
                raise ConflictError("Run requires the exact approved configuration.", code="ci_scope_not_approved")
            await self.published(session, document.workflow_version_id, document.user_id)
            if await self.active_run(session, watchlist_id):
                raise ConflictError("A run is already active for this watchlist.", code="watchlist_run_active")
            document.watchlist_id, document.watchlist_revision_id = watchlist_id, revision_id
            session.add(RunModel(**PostgresRunRepository._to_orm_values(document)))
            await session.flush()
            await PostgresRunRepository._create_task_execution_rows(session, document)
            return document

    async def runs(self, watchlist_id: str, owner_id: str, limit: int, offset: int) -> list[RunDocument]:
        async with self.session() as session:
            await self.owned(session, watchlist_id, owner_id)
            rows = (await session.execute(select(RunModel).where(RunModel.watchlist_id == watchlist_id,
                RunModel.user_id == owner_id).order_by(RunModel.created_at.desc(), RunModel.run_id)
                .limit(limit).offset(offset))).scalars()
            return [PostgresRunRepository._to_domain(row) for row in rows]
