"""CI configuration service/API/security contracts; no database or live model required."""

import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from pydantic import ValidationError as SchemaError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.api.dependencies import get_current_user_id, get_intelligence_service
from app.core.config import settings
from app.execution.state import Task
from app.execution.tools.contracts import parse_tool_result
from app.execution.tools.http_request_tool import http_request
from app.execution.tools.network_policy import validate_external_url
from app.main import app
from app.modules.competitive_intelligence.models import (
    CreateWatchlist, ProductProfile, Revision, StartRunRequest, Watchlist, WatchlistConfig, digest,
)
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.runs.models import RunDocument
from app.shared.collection_scope import collection_scope
from app.shared.errors import ConflictError, ResourceNotFoundError, ValidationError


def request_fixture(version_id: str = "version") -> CreateWatchlist:
    return CreateWatchlist(name="Competitors", config=WatchlistConfig(
        goal="Compare public pricing changes", dimensions=["pricing"], workflow_version_id=version_id,
        comparison_criteria=[{"field": "price", "objective": "Assess SMB affordability"}],
        products=[{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/",
                   "profile": {"target_segments": ["SMB"], "facts": [{"field": "price", "value": "$10/month"}]}},
                  {"name": "Competitor", "kind": "competitor", "official_website": "https://rival.invalid/",
                   "sources": [{"id": uuid4(), "url": "https://rival.invalid/pricing", "kind": "pricing"}]}]))


def watchlist_fixture() -> Watchlist:
    request = request_fixture()
    now, identity = datetime.now(timezone.utc), uuid4()
    for product in request.config.products:
        product.id, product.profile_version_id = uuid4(), uuid4()
    return Watchlist(id=identity, owner_id="owner", name=request.name, description="", status="active",
        created_at=now, updated_at=now, current_revision=Revision(id=uuid4(), watchlist_id=identity,
        revision_number=1, config=request.config, config_hash=digest(request.config),
        approval_status="approved", created_at=now))


class TestCIConfiguration(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.watchlist = watchlist_fixture()
        self.repository = AsyncMock()
        self.repository.get.return_value = self.watchlist
        self.repository.workflow_id.return_value = "workflow"
        self.runs = MagicMock()
        self.runs.run_repository.find_by_idempotency_key = AsyncMock(return_value=None)
        self.runs.create_workflow_run = AsyncMock(return_value=RunDocument(
            run_id=str(uuid4()), flow_id="workflow", user_id="owner", workflow_version_id="version", status="queued",
            plan=[Task(id=1, node="source_researcher", tool_ids=["crawl"], tool_names=["news_crawler"],
                       description="Collect approved sources", status="pending")]))
        self.runs.enqueue_admitted_run = AsyncMock(side_effect=lambda doc: doc)
        self.repository.admit_run.side_effect = lambda doc, *args: doc
        self.repository.pin_baselines = AsyncMock(return_value={})
        self.service = IntelligenceService(self.repository, self.runs)

    async def test_budget_policy_and_unknown_fields(self) -> None:
        request = request_fixture()
        request.config.budget.max_llm_calls = settings.MAX_RUN_LLM_CALLS + 1
        with self.assertRaises(ValidationError):
            await self.service.create(request, "owner")
        self.repository.create.assert_not_awaited()
        with self.assertRaises(SchemaError):
            CreateWatchlist.model_validate({**request_fixture().model_dump(), "approved": True})

    async def test_source_context_credentials_and_private_urls_rejected(self) -> None:
        for url in ("http://127.0.0.1/", "https://user:pass@public.invalid/", "http://localhost/", "https://x.invalid:8000/"):
            data = request_fixture().model_dump(mode="json")
            data["config"]["products"][1]["sources"][0]["url"] = url
            with self.assertRaises(SchemaError):
                CreateWatchlist.model_validate(data)
        data["config"]["products"][1]["sources"][0] = {
            "url": "https://rival.invalid/", "kind": "pricing", "context": {"headers": {"Authorization": "secret"}}}
        with self.assertRaises(SchemaError):
            CreateWatchlist.model_validate(data)

    async def test_profile_dates_are_separate_nullable_and_timezone_aware(self) -> None:
        profile = ProductProfile.model_validate({"launch_at": {"value": "2026-01-01T00:00:00Z"}})
        self.assertIsNone(profile.fetched_at)
        self.assertIsNone(profile.effective_at)
        with self.assertRaises(SchemaError):
            ProductProfile.model_validate({"launch_at": {"value": "2026-01-01T00:00:00"}})
        with self.assertRaises(SchemaError):
            ProductProfile.model_validate({"facts": [{"field": "price", "value": "10", "provenance": "verified"}]})

    async def test_prepare_freezes_goals_profiles_scope_budget_and_null_baselines(self) -> None:
        revision = self.watchlist.current_revision
        await self.service.prepare(self.watchlist, str(revision.id), "version")
        kwargs = self.runs.create_workflow_run.call_args.kwargs
        self.assertTrue(kwargs["defer_persistence"])
        frozen = kwargs["input_data"]["competitive_intelligence"]
        self.assertEqual(frozen["config_hash"], revision.config_hash)
        self.assertEqual(frozen["approved_urls"], ["https://rival.invalid/pricing"])
        self.assertTrue(all(value is None for value in frozen["baselines"].values()))
        self.assertEqual(frozen["config"]["products"][0]["profile_version_id"],
                         str(revision.config.products[0].profile_version_id))
        self.runs.enqueue_admitted_run.assert_not_awaited()

    async def test_unapproved_and_stale_revision_do_not_enqueue(self) -> None:
        request = StartRunRequest(revision_id=self.watchlist.current_revision.id, workflow_version_id="version")
        self.watchlist.current_revision.approval_status = "unapproved"
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request, "owner", None)
        self.watchlist.current_revision.approval_status = "approved"
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request.model_copy(update={"revision_id": uuid4()}),
                                     "owner", None)
        self.repository.admit_run.assert_not_awaited()

    async def test_unapproved_host_and_arbitrary_tool_scope_blocked(self) -> None:
        revision = self.watchlist.current_revision
        revision.config.products[1].sources[0].url = "https://unapproved.invalid/pricing"
        with self.assertRaises(ValidationError):
            await self.service.prepare(self.watchlist, str(revision.id), "version")
        revision.config.products[1].sources[0].url = "https://rival.invalid/pricing"
        self.runs.create_workflow_run.return_value.plan[0].tool_names = ["python_executor"]
        with self.assertRaises(ValidationError):
            await self.service.prepare(self.watchlist, str(revision.id), "version")

    async def test_resolved_catalog_ids_use_frozen_role_and_tool_names(self) -> None:
        document = self.runs.create_workflow_run.return_value
        document.plan[0].agent_id = "agent-uuid"
        document.plan[0].tool_ids = ["tool-uuid"]
        document.resolved_model_config = {"agent_profiles": {"agent-uuid": {"name": "source_researcher"}}}
        await self.service.prepare(self.watchlist, str(self.watchlist.current_revision.id), "version")

    async def test_start_only_enqueues_after_atomic_admission(self) -> None:
        request = StartRunRequest(revision_id=self.watchlist.current_revision.id, workflow_version_id="version")
        self.repository.admit_run.side_effect = ConflictError("Overlap")
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request, "owner", "one")
        self.runs.enqueue_admitted_run.assert_not_awaited()

    async def test_idempotency_retry_returns_original_after_revision_edit(self) -> None:
        revision = self.watchlist.current_revision.id
        request = StartRunRequest(revision_id=revision, workflow_version_id="version")
        document = self.runs.create_workflow_run.return_value
        document.idempotency_fingerprint = digest({"owner_id": "owner", "watchlist_id": str(self.watchlist.id),
                                                   "revision_id": str(revision), "workflow_version_id": "version"})
        self.runs.run_repository.find_by_idempotency_key.return_value = document
        self.watchlist.current_revision.approval_status = "unapproved"
        returned = await self.service.start(str(self.watchlist.id), request, "owner", "one")
        self.assertEqual(returned.run_id, document.run_id)
        self.repository.admit_run.assert_not_awaited()
        self.runs.enqueue_admitted_run.assert_not_awaited()
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request.model_copy(update={"revision_id": uuid4()}),
                                     "owner", "one")

    async def test_setup_proposal_uses_owned_chat_without_saving_or_approving(self) -> None:
        conversations, planner = AsyncMock(), AsyncMock()
        conversations.list_messages.return_value = [MagicMock(role="user", content="Track Rival public pricing")]
        planner.propose.return_value = request_fixture()
        service = IntelligenceService(self.repository, self.runs, conversations, planner)
        proposal = await service.propose(str(uuid4()), "owner")
        self.assertIsNone(proposal.config.workflow_version_id)
        self.assertIsNone(proposal.config.products[1].sources[0].id)
        self.repository.create.assert_not_awaited()
        self.runs.enqueue_admitted_run.assert_not_awaited()
        conversations.get.return_value = None
        with self.assertRaises(ResourceNotFoundError):
            await service.propose(str(uuid4()), "intruder")

    async def test_exact_url_scope_survives_async_to_thread(self) -> None:
        with collection_scope(["https://rival.invalid/pricing"]):
            self.assertIsNone(validate_external_url("https://rival.invalid/pricing")[1])
            self.assertIsNotNone(validate_external_url("https://rival.invalid/private")[1])
            blocked = await asyncio.to_thread(validate_external_url, "https://elsewhere.invalid/")
            self.assertIsNotNone(blocked[1])
        self.assertIsNone(validate_external_url("https://elsewhere.invalid/")[1])

    async def test_http_write_and_headers_blocked_before_transport(self) -> None:
        with collection_scope(["https://rival.invalid/pricing"]), patch("requests.request") as transport:
            for args in ({"method": "POST"}, {"headers": {"Authorization": "private"}}, {"data": "secret"}):
                result = parse_tool_result(http_request.invoke({"url": "https://rival.invalid/pricing", **args}))
                self.assertEqual(result.error.code, "ci_public_get_only")
            transport.assert_not_called()

    async def test_batch_crawl_propagates_scope_into_its_thread_pool(self) -> None:
        from app.execution.tools.news_crawler_batch_tool import news_crawler_batch
        with collection_scope(["https://rival.invalid/pricing"]), patch("requests.get") as transport:
            result = parse_tool_result(news_crawler_batch.invoke({"urls": ["https://elsewhere.invalid/private"]}))
            self.assertFalse(result.ok)
            transport.assert_not_called()

    async def test_api_commands_validate_auth_and_contracts(self) -> None:
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.dependency_overrides[get_intelligence_service] = lambda: self.service
        self.repository.create.return_value = self.watchlist
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/v1/watchlists", json=request_fixture().model_dump(mode="json"))
                self.assertEqual(response.status_code, 201, response.text)
                invalid = await client.post("/api/v1/watchlists", json={"name": "x", "approved": True})
                self.assertEqual(invalid.status_code, 422)
                self.repository.get.side_effect = ResourceNotFoundError("Not found")
                hidden = await client.get(f"/api/v1/watchlists/{uuid4()}")
                self.assertEqual(hidden.status_code, 404)
        finally:
            app.dependency_overrides.clear()
