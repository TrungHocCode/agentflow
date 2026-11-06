"""CI live-loop hooks; deterministic fixtures, no network or model."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from uuid import UUID, uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_coordinator import (
    coverage_status, plan_round, should_terminate,
)
from app.modules.competitive_intelligence.loop_hooks import CILoopHooks
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate
from app.modules.competitive_intelligence.models import (
    CreateWatchlist, InvestigationBudget, WatchlistConfig, digest,
)
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PRICING = ("# Pricing\n\nPro plan costs $10 per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n" * 4)


def config_fixture() -> WatchlistConfig:
    return CreateWatchlist(name="W", config=WatchlistConfig(
        goal="Track pricing", dimensions=["pricing"], workflow_version_id="v",
        comparison_criteria=[{"field": "price", "objective": "Assess affordability"}],
        products=[{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                  {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                   "sources": [{"id": uuid4(), "url": "https://rival.invalid/pricing", "kind": "pricing"}]}])
    ).config


def frozen_fixture(config=None) -> dict:
    config = config or config_fixture()
    enabled = [source for product in config.products for source in product.sources if source.enabled]
    return {"schema_version": "1", "watchlist_id": str(uuid4()), "revision_id": str(uuid4()),
            "config_hash": digest(config), "config": config.model_dump(mode="json"),
            "approved_urls": [source.url for source in enabled],
            "baselines": {str(source.id): None for source in enabled},
            "baseline_policy": "pinned_at_acceptance"}


def run_fixture(frozen=None, plan=()) -> RunDocument:
    return RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner", workflow_version_id="v",
                       status="running", plan=list(plan),
                       input_data={"competitive_intelligence": frozen or frozen_fixture(),
                                   "user_prompt": "Track pricing", "urls": ["https://rival.invalid/pricing"]},
                       metadata={"ci_scope": "approved_exact_urls"})


def researcher_task(task_id=1) -> Task:
    return Task(id=task_id, node="source_researcher", agent_id="source_researcher",
                tool_names=["news_crawler"], description="Collect", status="done")


def hooks_fixture() -> CILoopHooks:
    return CILoopHooks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), MagicMock())


class TestLoopScope(unittest.TestCase):
    def test_non_ci_runs_and_roles_are_ignored(self) -> None:
        hooks = hooks_fixture()
        plain = RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner", status="running", plan=[])
        self.assertIsNone(CILoopHooks.ci_scope(plain))
        scoped = run_fixture()
        self.assertIn("approved_urls", CILoopHooks.ci_scope(scoped))
        scoped.input_data["competitive_intelligence"] = "frozen-string"
        self.assertIsNone(CILoopHooks.ci_scope(scoped))


class TestLoopWiring(unittest.TestCase):
    def test_live_loop_receives_snapshot_service(self) -> None:
        """The hook drives domain operations, so it must get the service, not the repository."""
        from app.infrastructure.container import build_ci_loop_hooks

        hooks = build_ci_loop_hooks(MagicMock())
        self.assertTrue(hasattr(hooks.snapshots, "capture"))
        self.assertTrue(hasattr(hooks.snapshots, "compare_source"))
        self.assertTrue(hasattr(hooks.snapshots, "promote"))


class TestTaskHook(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hooks = hooks_fixture()
        self.frozen = frozen_fixture()
        self.source_id = next(iter(self.frozen["baselines"]))
        self.run = run_fixture(self.frozen, [researcher_task()])
        self.hooks.research.list_results = AsyncMock(return_value=[])
        self.hooks.artifacts.resolve.return_value.read_text.return_value = PRICING

    def document(self, url: str, task_id: int = 1):
        record = MagicMock()
        record.task_id = task_id
        record.metadata = {"kind": "source_document"}
        record.content = {"source_url": url, "storage_uri": "runs/o/r/sources/doc.txt"}
        return record

    async def test_approved_fetch_captures_compares_and_appends_round(self) -> None:
        snapshot = MagicMock()
        snapshot.id = uuid4()
        self.hooks.snapshots.capture = AsyncMock(return_value=(MagicMock(), snapshot))
        comparison = MagicMock()
        comparison.outcome = "changed"
        candidate = MagicMock(id=uuid4())
        self.hooks.snapshots.compare_source = AsyncMock(return_value=(comparison, [candidate]))
        self.hooks.snapshots.finalize_source = AsyncMock(return_value=comparison)
        self.hooks.investigations.list_rounds = AsyncMock(return_value=[])
        planned = MagicMock()
        self.hooks.investigations.propose_round = AsyncMock(return_value=(planned, "accepted"))
        followup = Task(id=9, node="evidence_verifier", description="Verify", status="pending")
        self.hooks.investigations.materialize_tasks = AsyncMock(return_value=[followup])
        self.hooks.research.list_results = AsyncMock(
            return_value=[self.document("https://rival.invalid/pricing")])
        tasks = await self.hooks.on_task_completed(self.run, researcher_task(), "done")
        self.assertEqual(tasks, [followup])
        kwargs = self.hooks.snapshots.capture.call_args.kwargs
        self.assertEqual(kwargs["requested_url"], "https://rival.invalid/pricing")
        self.assertEqual(kwargs["owner_id"], "owner")

    async def test_unapproved_urls_never_capture(self) -> None:
        self.hooks.research.list_results = AsyncMock(
            return_value=[self.document("https://evil.invalid/pricing")])
        tasks = await self.hooks.on_task_completed(self.run, researcher_task(), "done")
        self.assertEqual(tasks, [])
        self.hooks.snapshots.capture.assert_not_awaited()

    async def test_www_redirect_resolves_to_approved_entry(self) -> None:
        snapshot = MagicMock()
        snapshot.id = uuid4()
        self.hooks.snapshots.capture = AsyncMock(return_value=(MagicMock(), snapshot))
        comparison = MagicMock()
        comparison.outcome = "no_change"
        self.hooks.snapshots.compare_source = AsyncMock(return_value=(comparison, []))
        self.hooks.snapshots.finalize_source = AsyncMock(return_value=comparison)
        self.hooks.research.list_results = AsyncMock(
            return_value=[self.document("https://www.rival.invalid/pricing")])
        tasks = await self.hooks.on_task_completed(self.run, researcher_task(), "done")
        self.assertEqual(tasks, [])
        kwargs = self.hooks.snapshots.capture.call_args.kwargs
        self.assertEqual(kwargs["requested_url"], "https://rival.invalid/pricing")
        self.assertEqual(kwargs["final_url"], "https://www.rival.invalid/pricing")

    async def test_no_change_or_rejected_round_appends_nothing(self) -> None:
        snapshot = MagicMock()
        snapshot.id = uuid4()
        self.hooks.snapshots.capture = AsyncMock(return_value=(MagicMock(), snapshot))
        comparison = MagicMock()
        comparison.outcome = "no_change"
        self.hooks.snapshots.compare_source = AsyncMock(return_value=(comparison, []))
        self.hooks.snapshots.finalize_source = AsyncMock(return_value=comparison)
        self.hooks.research.list_results = AsyncMock(
            return_value=[self.document("https://rival.invalid/pricing")])
        tasks = await self.hooks.on_task_completed(self.run, researcher_task(), "done")
        self.assertEqual(tasks, [])
        self.hooks.investigations.propose_round.assert_not_awaited()

    async def test_catalog_uuid_task_resolves_role_and_captures(self) -> None:
        """A workflow-seeded task carries the catalog UUID, not the role name."""
        provider = AsyncMock()
        provider.get_agent = AsyncMock(return_value=SimpleNamespace(name="source_researcher"))
        self.hooks.agent_profiles = provider
        snapshot = MagicMock()
        snapshot.id = uuid4()
        self.hooks.snapshots.capture = AsyncMock(return_value=(MagicMock(), snapshot))
        comparison = MagicMock()
        comparison.outcome = "no_change"
        self.hooks.snapshots.compare_source = AsyncMock(return_value=(comparison, []))
        self.hooks.snapshots.finalize_source = AsyncMock(return_value=comparison)
        self.hooks.research.list_results = AsyncMock(
            return_value=[self.document("https://rival.invalid/pricing")])
        agent_uuid = str(uuid4())
        task = Task(id=1, node=agent_uuid, agent_id=agent_uuid, tool_names=["news_crawler"],
                    description="Collect", status="done")
        await self.hooks.on_task_completed(self.run, task, "done")
        provider.get_agent.assert_awaited_with(agent_uuid)
        self.hooks.snapshots.capture.assert_awaited()


class TestRoundsReachTheAnalyst(unittest.TestCase):
    """The real coordinator, fed what the hook builds: changes must reach the analyst and end coverage."""

    @staticmethod
    def candidate(source_id) -> ChangeCandidate:
        now = datetime.now(timezone.utc)
        return ChangeCandidate(id=uuid4(), run_id=uuid4(), source_id=source_id,
                               before_snapshot_id=uuid4(), after_snapshot_id=uuid4(), kind="modified",
                               section="Pricing", before_excerpt="$5", after_excerpt="$7",
                               diff_algorithm_version="v1", diff_hash="a" * 64, detected_at=now)

    def plan(self, scope, candidates):
        questions = CILoopHooks._questions(scope, UUID(scope["watchlist_id"]), candidates)
        return questions, plan_round(uuid4(), UUID(scope["watchlist_id"]), UUID(scope["revision_id"]), 1, None,
                                     questions, candidates, CILoopHooks._round_scope(scope),
                                     InvestigationBudget(), False, datetime.now(timezone.utc))

    def test_changed_source_gets_an_analyst_after_its_researcher(self) -> None:
        scope = frozen_fixture()
        source_id = UUID(next(iter(scope["baselines"])))
        _, planned = self.plan(scope, [self.candidate(source_id)])

        roles = [task.role for task in planned.tasks]
        self.assertEqual(roles, ["source_researcher", "evidence_verifier", "competitive_analyst"])
        analyst = planned.tasks[-1]
        self.assertEqual(analyst.depends_on_keys, [planned.tasks[0].task_key])
        self.assertTrue(analyst.question_ids)
        self.assertTrue(analyst.candidate_ids)

    def test_accepted_round_completes_coverage_so_the_loop_stops(self) -> None:
        scope = frozen_fixture()
        source_id = UUID(next(iter(scope["baselines"])))
        questions, planned = self.plan(scope, [self.candidate(source_id)])
        self.assertFalse(should_terminate(1, coverage_status(questions, []), InvestigationBudget(), 0)[0])
        finished, reason = should_terminate(2, coverage_status(questions, [planned]), InvestigationBudget(), 0)
        self.assertTrue(finished)
        self.assertEqual(reason, "coverage_complete")


class TestRunFailedHook(unittest.IsolatedAsyncioTestCase):
    async def test_failed_ci_run_notifies_its_owner(self) -> None:
        notifications = AsyncMock()
        hooks = CILoopHooks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), MagicMock(),
                            notifications=notifications)
        run = run_fixture()
        run.error_message = "The machine is out of memory."
        await hooks.on_run_failed(run)

        args = notifications.notify.await_args.args
        self.assertEqual(args[0:2], ("owner", "run_failed"))
        self.assertEqual(args[3], "The machine is out of memory.")
        self.assertEqual(args[5], run.run_id)

    async def test_other_runs_do_not_notify(self) -> None:
        notifications = AsyncMock()
        hooks = CILoopHooks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), MagicMock(),
                            notifications=notifications)
        plain = RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner", status="failed", plan=[])
        await hooks.on_run_failed(plain)
        notifications.notify.assert_not_awaited()


class TestRunHook(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hooks = hooks_fixture()
        self.frozen = frozen_fixture()
        self.run = run_fixture(self.frozen)

    async def test_non_ci_run_is_ignored(self) -> None:
        plain = RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner", status="completed", plan=[])
        await self.hooks.on_run_completed(plain)
        self.hooks.snapshots.list_comparisons.assert_not_awaited()
        self.hooks.briefs.build_brief.assert_not_awaited()

    async def test_completed_run_promotes_and_builds_brief(self) -> None:
        from app.modules.competitive_intelligence.snapshot_contracts import RunSourceComparison
        source_id = UUID(next(iter(self.frozen["baselines"])))
        comparison = RunSourceComparison(run_id=uuid4(), source_id=source_id,
            baseline_snapshot_id=None, current_snapshot_id=uuid4(), outcome="changed", quality="complete",
            reason_codes=[], decided_at=NOW)
        self.hooks.snapshots.list_comparisons = AsyncMock(return_value=[comparison])
        self.hooks.snapshots.get_snapshot = AsyncMock(return_value=MagicMock())
        self.hooks.snapshots.promote = AsyncMock(return_value="promoted")
        self.hooks.snapshots.set_promotion = AsyncMock(return_value=comparison)
        self.hooks.briefs.build_brief = AsyncMock(return_value=MagicMock())
        await self.hooks.on_run_completed(self.run)
        self.hooks.snapshots.promote.assert_awaited_once()
        self.hooks.snapshots.set_promotion.assert_awaited_once()
        self.hooks.briefs.build_brief.assert_awaited_once()

    async def test_unavailable_comparison_skips_promotion_but_builds(self) -> None:
        comparison = MagicMock()
        comparison.outcome = "unavailable"
        comparison.source_id = next(iter(self.frozen["baselines"]))
        comparison.current_snapshot_id = None
        self.hooks.snapshots.list_comparisons = AsyncMock(return_value=[comparison])
        self.hooks.snapshots.set_promotion = AsyncMock(return_value=comparison)
        self.hooks.briefs.build_brief = AsyncMock(return_value=MagicMock())
        await self.hooks.on_run_completed(self.run)
        self.hooks.snapshots.promote.assert_not_awaited()
        args = self.hooks.snapshots.set_promotion.call_args.args
        self.assertEqual(args[3], "skipped")


class TestObserverPlumbing(unittest.IsolatedAsyncioTestCase):
    def service(self, task_observers=None, run_observers=None) -> RunService:
        return RunService(MagicMock(), MagicMock(), MagicMock(), task_observers=task_observers,
                          run_observers=run_observers)

    async def test_failing_task_observer_cannot_break_the_run(self) -> None:
        from types import SimpleNamespace
        async def boom(run, task, status):
            raise RuntimeError("hook exploded")
        service = self.service(task_observers=[SimpleNamespace(on_task_completed=boom)])
        run = run_fixture(frozen_fixture(), [researcher_task()])
        events = await service._notify_task_completed(
            run, {"result_storage": [{"task_id": 1, "status": "done", "result": "ok"}]})
        self.assertEqual(events, [])
        self.assertTrue(any("hook failed" in str(log) for log in run.logs))

    async def test_followup_tasks_merge_into_plan(self) -> None:
        from types import SimpleNamespace
        async def seed(run, task, status):
            return [Task(id=7, node="evidence_verifier", description="Verify", status="pending")]
        service = self.service(task_observers=[SimpleNamespace(on_task_completed=seed)])
        run = run_fixture(frozen_fixture(), [researcher_task()])
        events = await service._notify_task_completed(
            run, {"result_storage": [{"task_id": 1, "status": "done", "result": "ok"}]})
        self.assertEqual([task.id for task in run.plan], [1, 7])
        self.assertEqual(len(events), 1)

    async def test_failing_run_observer_cannot_rewrite_terminal_state(self) -> None:
        from types import SimpleNamespace
        async def boom(run):
            raise RuntimeError("hook exploded")
        service = self.service(run_observers=[SimpleNamespace(on_run_completed=boom)])
        run = run_fixture()
        run.status = "completed"
        await service._notify_run_completed(run)
        self.assertEqual(run.status, "completed")
        self.assertTrue(any("hook failed" in str(log) for log in run.logs))

    def test_migration_chain_links_0009_to_0010(self) -> None:
        import importlib.util
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic",
                                             "versions", "0010_ci_comparison_promotion.py"))
        spec = importlib.util.spec_from_file_location("migration_0010", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0009_ci_briefs")
        self.assertEqual(module.revision, "0010_ci_comparison_promotion")


if __name__ == "__main__":
    unittest.main()
