"""CI use cases: configuration is proposed, reviewed, frozen and atomically admitted."""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import settings
from app.modules.competitive_intelligence.models import (
    CreateWatchlist, Page, Pagination, ProductProfileVersion, ProductView, Revision, SourceView, StartRunRequest,
    UpdateWatchlist,
    Watchlist, digest,
)
from app.modules.competitive_intelligence.ports import IntelligenceRepository, SetupPlanner
from app.modules.conversations.ports import ConversationRepository
from app.modules.runs.models import RunDocument, RunResponse
from app.modules.runs.service import RunService
from app.shared.errors import ConflictError, ResourceNotFoundError, ValidationError

SAFE_ROLES = {"source_researcher", "synthesis_agent", "report_agent"}
SAFE_TOOLS = {"news_crawler", "news_crawler_batch", "http_request", "text_summarizer",
              "markdown_report_generator", "chart_generator"}


class IntelligenceService:
    def __init__(self, repository: IntelligenceRepository, runs: RunService,
                 conversations: ConversationRepository | None = None, planner: SetupPlanner | None = None) -> None:
        self.repository, self.run_service = repository, runs
        self.conversations, self.planner = conversations, planner

    @staticmethod
    def validate_budget(request: CreateWatchlist) -> None:
        budget = request.config.budget
        if (budget.max_llm_calls > settings.MAX_RUN_LLM_CALLS
                or budget.max_total_tokens > settings.MAX_RUN_ESTIMATED_TOKENS
                or budget.max_duration_seconds > settings.MAX_RUN_DURATION):
            raise ValidationError("Requested budget exceeds operator limits.", code="ci_budget_exceeds_policy")

    async def create(self, request: CreateWatchlist, owner_id: str) -> Watchlist:
        self.validate_budget(request)
        return await self.repository.create(request, owner_id)

    async def get(self, identity: str, owner_id: str) -> Watchlist:
        return await self.repository.get(identity, owner_id)

    async def list(self, owner_id: str, limit: int, offset: int) -> Page[Watchlist]:
        return Page[Watchlist](items=await self.repository.list(owner_id, limit, offset),
            pagination=Pagination(limit=limit, offset=offset, total=await self.repository.count(owner_id)))

    async def revisions(self, identity: str, owner_id: str) -> list[Revision]:
        return await self.repository.revisions(identity, owner_id)

    async def profiles(self, identity: str, product_id: str, owner_id: str) -> list[ProductProfileVersion]:
        return await self.repository.profiles(identity, product_id, owner_id)

    async def archive(self, identity: str, owner_id: str) -> None:
        await self.repository.archive(identity, owner_id)

    async def runs(self, identity: str, owner_id: str, limit: int, offset: int) -> Page[RunResponse]:
        documents = await self.repository.runs(identity, owner_id, limit, offset)
        return Page[RunResponse](items=[RunResponse(**doc.model_dump()) for doc in documents],
            pagination=Pagination(limit=limit, offset=offset, total=await self.repository.count(owner_id, identity)))

    async def update(self, identity: str, request: UpdateWatchlist, owner_id: str) -> Watchlist:
        self.validate_budget(request)
        return await self.repository.update(identity, owner_id, request)

    async def propose(self, conversation_id: str, owner_id: str) -> CreateWatchlist:
        if self.conversations is None or self.planner is None:
            raise ValidationError("Setup proposal inference is unavailable.")
        conversation = await self.conversations.get(conversation_id, owner_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation was not found.", entity="conversation")
        messages = await self.conversations.list_messages(conversation_id, limit=20)
        proposal = await self.planner.propose([{"role": message.role, "content": message.content}
                                             for message in messages][-10:])
        # A proposal cannot link existing identities/version/evidence invented by the model.
        for product in proposal.config.products:
            product.id, product.profile_version_id = None, None
            for source in product.sources:
                source.id = None
            if any(fact.evidence_ids for fact in product.profile.facts):
                raise ValidationError("Setup proposal cannot invent evidence references.")
            for field in ("launch_at", "published_at", "effective_at", "observed_at", "fetched_at"):
                date = getattr(product.profile, field)
                if date and date.evidence_ids:
                    raise ValidationError("Setup proposal cannot invent date evidence.")
        proposal.config.workflow_version_id = None
        self.validate_budget(proposal)
        return proposal

    @staticmethod
    def ready(watchlist: Watchlist, revision_id: str, version_id: str) -> None:
        revision = watchlist.current_revision
        if watchlist.status != "active" or str(revision.id) != revision_id:
            raise ConflictError("Review the current active revision.", code="stale_watchlist_revision")
        config = revision.config
        if config.workflow_version_id != version_id:
            raise ConflictError("Workflow version does not match this revision.", code="ci_workflow_mismatch")
        if not config.comparison_criteria or not any(p.kind == "own" for p in config.products):
            raise ValidationError("Specify an own product and explicit comparison criteria before approval.")
        if not any(p.kind == "competitor" for p in config.products):
            raise ValidationError("Specify at least one competitor.")
        enabled = [source for product in config.products for source in product.sources if source.enabled]
        if not enabled:
            raise ValidationError("At least one approved public source is required.")
        for product in config.products:
            for source in product.sources:
                if source.enabled and (not product.official_website or
                        urlsplit(source.url).hostname != urlsplit(product.official_website).hostname):
                    raise ValidationError("Enabled sources must match the product's declared official website host.")

    @staticmethod
    def validate_execution(document: RunDocument, watchlist: Watchlist) -> None:
        config = watchlist.current_revision.config
        if len(document.plan) > config.budget.max_tasks:
            raise ValidationError("Workflow exceeds the approved task budget.")
        for task in document.plan:
            profile = document.resolved_model_config.get("agent_profiles", {}).get(task.agent_id or task.node, {})
            role = profile.get("name", task.agent_id or task.node)
            if role not in SAFE_ROLES or not task.tool_names or not set(task.tool_names) <= SAFE_TOOLS:
                raise ValidationError("CI workflows require explicit supported roles and read-only/render tool scope.",
                                      code="ci_workflow_scope_invalid")

    async def prepare(self, watchlist: Watchlist, revision_id: str, version_id: str) -> RunDocument:
        self.ready(watchlist, revision_id, version_id)
        workflow_id = await self.repository.workflow_id(version_id, watchlist.owner_id)
        config = watchlist.current_revision.config
        enabled = [source for product in config.products for source in product.sources if source.enabled]
        pinned = await self.repository.pin_baselines(str(watchlist.id), watchlist.owner_id)
        frozen = {"schema_version": "1", "watchlist_id": str(watchlist.id), "revision_id": revision_id,
                  "config_hash": watchlist.current_revision.config_hash, "config": config.model_dump(mode="json"),
                  "approved_urls": [source.url for source in enabled],
                  "baselines": {str(source.id): pinned.get(str(source.id)) for source in enabled},
                  "baseline_policy": "pinned_at_acceptance"}
        document = await self.run_service.create_workflow_run(workflow_id, version_id,
            user_id=watchlist.owner_id, input_data={"competitive_intelligence": frozen,
                "user_prompt": config.goal, "urls": [source.url for source in enabled]},
            metadata={"ci_scope": "approved_exact_urls"}, defer_persistence=True)
        if document is None:
            raise ConflictError("The published workflow is no longer available.", code="ci_workflow_not_published")
        self.validate_execution(document, watchlist)
        return document

    async def approve(self, identity: str, revision_id: str, version_id: str, owner_id: str) -> Revision:
        watchlist = await self.repository.get(identity, owner_id)
        await self.prepare(watchlist, revision_id, version_id)
        return await self.repository.approve(identity, revision_id, owner_id, version_id)

    async def start(self, identity: str, request: StartRunRequest, owner_id: str,
                    idempotency_key: str | None) -> RunDocument:
        watchlist = await self.repository.get(identity, owner_id)
        fingerprint = digest({"owner_id": owner_id, "watchlist_id": identity,
            "revision_id": str(request.revision_id), "workflow_version_id": request.workflow_version_id})
        if idempotency_key:
            existing = await self.run_service.run_repository.find_by_idempotency_key(idempotency_key, owner_id)
            if existing is not None:
                if existing.idempotency_fingerprint != fingerprint:
                    raise ConflictError("Idempotency key was used for a different request.",
                                        code="idempotency_key_reused")
                return existing
        if watchlist.current_revision.approval_status != "approved":
            raise ConflictError("Review and approve the current revision first.", code="ci_scope_not_approved")
        document = await self.prepare(watchlist, str(request.revision_id), request.workflow_version_id)
        document.idempotency_key = idempotency_key
        document.idempotency_fingerprint = fingerprint
        admitted = await self.repository.admit_run(document, identity, str(request.revision_id))
        if admitted.run_id != document.run_id:
            return admitted
        return await self.run_service.enqueue_admitted_run(admitted)

    async def products(self, identity: str, owner_id: str) -> list[ProductView]:
        watchlist = await self.repository.get(identity, owner_id)
        return [ProductView(**product.model_dump(), watchlist_id=watchlist.id)
                for product in watchlist.current_revision.config.products]

    async def sources(self, identity: str, owner_id: str) -> list[SourceView]:
        watchlist = await self.repository.get(identity, owner_id)
        return [SourceView(**source.model_dump(), watchlist_id=watchlist.id, product_id=product.id,
                           config_version=digest(source))
                for product in watchlist.current_revision.config.products for source in product.sources]
