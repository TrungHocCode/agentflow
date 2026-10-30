"""Composition helpers for wiring concrete adapters into application services."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.adapters import LangGraphExecutionAdapter
from app.execution.agents.resolver import AgentResolver
from app.execution.ports import ExecutionPort
from app.infrastructure.health_probe import DatabaseHealthProbe
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.conversation_repository import (
    PostgresConversationRepository,
)
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.postgres.identity_repository import PostgresUserRepository
from app.infrastructure.postgres.results_repository import PostgresResearchRepository
from app.infrastructure.postgres.agent_profile_provider import PostgresAgentProfileProvider
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.infrastructure.artifacts.evidence_store import DurableEvidenceStore
from app.infrastructure.redis.event_publisher import (
    RedisRunEventPublisher,
    get_in_memory_publisher,
)
from app.infrastructure.redis.run_queue import (
    RedisRunCommandQueue,
    get_in_memory_queue,
)
from app.infrastructure.redis.conversation_event_publisher import (
    RedisConversationEventPublisher,
    get_in_memory_conversation_publisher,
)
from app.modules.catalog.service import CatalogService
from app.modules.conversations.service import ConversationService
from app.modules.runs.events import RunEventPublisher
from app.modules.runs.queue import RunCommandQueue
from app.modules.runs.service import RunService
from app.modules.system.health import HealthService
from app.modules.workflows.service import WorkflowService
from app.modules.identity.service import IdentityService
from app.modules.results.service import ResearchResultService
from app.core.config import settings
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_snapshot_repository import PostgresSnapshotRepository
from app.infrastructure.postgres.intelligence_investigation_repository import PostgresInvestigationRepository
from app.infrastructure.postgres.intelligence_brief_repository import PostgresBriefRepository
from app.infrastructure.postgres.intelligence_schedule_repository import PostgresScheduleRepository
from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
from app.infrastructure.artifacts.brief_blobs import BriefFileStore
from app.infrastructure.ci_setup_planner import OllamaSetupPlanner
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.scheduling_service import SchedulingService
from app.modules.competitive_intelligence.timeline_service import TimelineService
from app.modules.competitive_intelligence.history_qa import HistoryQA
from app.modules.competitive_intelligence.loop_hooks import CILoopHooks


def build_execution_port() -> ExecutionPort:
    """Return the current in-process execution adapter."""

    return LangGraphExecutionAdapter(
        agent_resolver=AgentResolver(provider=PostgresAgentProfileProvider()),
        evidence_store=DurableEvidenceStore(PostgresResearchRepository(), settings.ARTIFACT_ROOT,
                                           PostgresRunRepository()),
    )


def build_health_service() -> HealthService:
    """Compose the operational health service."""

    return HealthService(probe=DatabaseHealthProbe())


def build_run_queue() -> RunCommandQueue:
    """Select the Redis queue or deterministic local queue."""

    if settings.REPOSITORY_BACKEND == "memory":
        return get_in_memory_queue()
    return RedisRunCommandQueue()


def build_event_publisher() -> RunEventPublisher:
    """Select live fan-out implementation for the current deployment."""

    if settings.REPOSITORY_BACKEND == "memory":
        return get_in_memory_publisher()
    return RedisRunEventPublisher()


def build_catalog_service(session: AsyncSession) -> CatalogService:
    """Compose the Catalog application service."""

    return CatalogService(repository=PostgresCatalogRepository(session))


def build_workflow_service(session: AsyncSession) -> WorkflowService:
    """Compose the Workflow application service."""

    return WorkflowService(
        repository=PostgresWorkflowRepository(session),
        catalog_repository=PostgresCatalogRepository(session),
    )


def build_auth_service(session: AsyncSession) -> IdentityService:
    """Compose the identity service."""

    return IdentityService(
        repository=PostgresUserRepository(session),
        signing_secret=settings.AUTH_SIGNING_SECRET,
        token_ttl=settings.ACCESS_TOKEN_TTL,
    )


def build_conversation_service(session: AsyncSession | None = None) -> ConversationService:
    """Compose the Build Phase conversation service."""

    return ConversationService(
        repository=PostgresConversationRepository(session),
        execution_port=build_execution_port(),
        workflow_repository=PostgresWorkflowRepository(session),
        event_publisher=(
            get_in_memory_conversation_publisher()
            if settings.REPOSITORY_BACKEND == "memory"
            else RedisConversationEventPublisher()
        ),
    )


def build_run_service(session: AsyncSession | None = None) -> RunService:
    """Compose RunService without leaking concrete adapters into the module."""

    service = RunService(
        run_repository=PostgresRunRepository(session),
        workflow_repository=PostgresWorkflowRepository(session) if session else None,
        execution_port=build_execution_port(),
        command_queue=build_run_queue(),
        event_publisher=build_event_publisher(),
        research_repository=PostgresResearchRepository(session),
        artifact_storage=LocalArtifactStorage(),
        conversation_repository=PostgresConversationRepository(session),
        catalog_repository=PostgresCatalogRepository(session),
    )
    hooks = build_ci_loop_hooks(service)
    service.task_observers.append(hooks)
    service.run_observers.append(hooks)
    return service


def build_ci_loop_hooks(run_service: RunService) -> CILoopHooks:
    """Compose CI live-loop hooks over durable snapshot/round/brief and research storage."""
    snapshot_repository = PostgresSnapshotRepository(blobs=SnapshotFileStore())
    # The live loop drives domain operations (capture/compare/promote), so it needs
    # the snapshot service, not the raw repository.
    snapshots = SnapshotService(snapshot_repository)
    briefs = BriefService(PostgresBriefRepository(), snapshot_repository, BriefFileStore(), run_service)
    return CILoopHooks(snapshots, InvestigationService(PostgresInvestigationRepository(), snapshot_repository),
                       briefs, PostgresResearchRepository(), LocalArtifactStorage(),
                       agent_profiles=PostgresAgentProfileProvider())


def build_research_result_service(session: AsyncSession | None = None) -> ResearchResultService:
    """Compose the structured research output query service."""

    return ResearchResultService(PostgresResearchRepository(session))


def build_intelligence_service(session: AsyncSession) -> IntelligenceService:
    return IntelligenceService(PostgresIntelligenceRepository(), build_run_service(session),
                               PostgresConversationRepository(), OllamaSetupPlanner())


def build_snapshot_service() -> SnapshotService:
    """Compose snapshot capture/compare/promote over the configured artifact root."""
    return SnapshotService(PostgresSnapshotRepository(blobs=SnapshotFileStore()))


def build_investigation_service() -> InvestigationService:
    """Compose round planning/acceptance over durable round and snapshot storage."""
    return InvestigationService(PostgresInvestigationRepository(),
                                PostgresSnapshotRepository(blobs=SnapshotFileStore()))


def build_brief_service(session: AsyncSession) -> BriefService:
    """Compose brief assembly/rendering over durable brief and snapshot storage."""
    return BriefService(PostgresBriefRepository(), PostgresSnapshotRepository(blobs=SnapshotFileStore()),
                        BriefFileStore(), build_run_service(session))


def build_scheduling_service(session: AsyncSession | None = None) -> SchedulingService:
    """Compose schedule firing over durable schedules and the approved start path."""
    return SchedulingService(PostgresScheduleRepository(), build_intelligence_service(session))


def build_timeline_service(session: AsyncSession | None = None) -> TimelineService:
    """Compose timeline assembly over snapshot, round, brief and watchlist storage."""
    snapshots = PostgresSnapshotRepository(blobs=SnapshotFileStore())
    intelligence = build_intelligence_service(session)
    return TimelineService(snapshots, InvestigationService(PostgresInvestigationRepository(), snapshots),
                           BriefService(PostgresBriefRepository(), snapshots, BriefFileStore(),
                                        build_run_service(session)), intelligence)


def build_history_qa(session: AsyncSession | None = None) -> HistoryQA:
    """Compose structured history Q&A over the timeline and snapshot retrieval."""
    timeline = build_timeline_service(session)
    snapshots = PostgresSnapshotRepository(blobs=SnapshotFileStore())
    return HistoryQA(timeline, snapshots, timeline.intelligence)
