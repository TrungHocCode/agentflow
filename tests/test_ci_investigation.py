"""CI-P4 bounded multi-agent investigation; deterministic fixtures, no network or model."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from pydantic import ValidationError as SchemaError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import (
    CI_ROLE_TOOLS, InvestigationQuestion, InvestigationRound, RoundScope, RoundTask,
)
from app.modules.competitive_intelligence.investigation_coordinator import (
    coverage_status, plan_round, reconstruct_after_restart, round_fits_budget, should_terminate,
    to_runtime_tasks, validate_round,
)
from app.modules.competitive_intelligence.models import InvestigationBudget

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
SOURCE_A = uuid4()


def scope_fixture(source_ids=(SOURCE_A,)) -> RoundScope:
    return RoundScope(approved_source_ids=list(source_ids),
                      approved_urls=["https://rival.invalid/pricing"], config_hash="c" * 64)


def budget_fixture(**overrides) -> InvestigationBudget:
    values = {"max_rounds": 3, "max_tasks": 20, "max_llm_calls": 64, "max_total_tokens": 262144,
              "max_duration_seconds": 3600}
    values.update(overrides)
    return InvestigationBudget(**values)


def candidate_fixture(source_id=SOURCE_A, section="Pricing") -> object:
    from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate
    from app.modules.competitive_intelligence.snapshot_normalize import hash_text
    before, after = uuid4(), uuid4()
    return ChangeCandidate(id=uuid4(), run_id=uuid4(), source_id=source_id, before_snapshot_id=before,
        after_snapshot_id=after, kind="modified", section=section, before_start=0, before_end=10,
        after_start=0, after_end=12, before_excerpt="Pro costs $10.", after_excerpt="Pro costs $12.",
        diff_algorithm_version="ci-section-diff-v1", diff_hash=hash_text(str(after)), detected_at=NOW)


def question_fixture(candidate_ids=()) -> InvestigationQuestion:
    return InvestigationQuestion(id=uuid4(), text="Did Rival pricing change for SMB plans?",
                                 dimension="pricing", candidate_ids=list(candidate_ids))


class TestPlanRound(unittest.TestCase):
    def test_researcher_and_verifier_per_candidate_with_real_refs(self) -> None:
        candidate = candidate_fixture()
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate], scope_fixture(),
                           budget_fixture(), False, NOW)
        self.assertEqual(len(round.tasks), 2)
        researcher, verifier = round.tasks
        self.assertEqual((researcher.role, verifier.role), ("source_researcher", "evidence_verifier"))
        self.assertEqual(verifier.depends_on_keys, [researcher.task_key])
        for task in round.tasks:
            self.assertIn(candidate.id, task.candidate_ids)
            self.assertIn(candidate.source_id, task.source_ids)
            self.assertNotIn("coordinator", task.role)

    def test_analyst_joins_only_where_questions_need_it(self) -> None:
        candidate = candidate_fixture()
        question = question_fixture([candidate.id])
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [question], [candidate], scope_fixture(),
                           budget_fixture(), False, NOW)
        roles = [task.role for task in round.tasks]
        self.assertIn("competitive_analyst", roles)
        analyst = next(task for task in round.tasks if task.role == "competitive_analyst")
        self.assertIn(question.id, analyst.question_ids)
        lone = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate], scope_fixture(),
                          budget_fixture(), False, NOW)
        self.assertNotIn("competitive_analyst", [task.role for task in lone.tasks])

    def test_new_evidence_changes_the_accepted_path_and_rounds_stay_bounded(self) -> None:
        first = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate_fixture()], scope_fixture(),
                           budget_fixture(), False, NOW)
        second = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [],
                            [candidate_fixture(), candidate_fixture(section="Features")], scope_fixture(),
                            budget_fixture(), False, NOW)
        self.assertGreater(len(second.tasks), len(first.tasks))
        many = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [],
                          [candidate_fixture(section=f"S{i}") for i in range(30)], scope_fixture(),
                          budget_fixture(), False, NOW)
        self.assertLessEqual(len(many.tasks), 20)

    def test_final_round_appends_report_depending_on_all(self) -> None:
        round = plan_round(uuid4(), uuid4(), uuid4(), 2, uuid4(), [], [candidate_fixture()], scope_fixture(),
                           budget_fixture(), True, NOW)
        report = round.tasks[-1]
        self.assertEqual(report.role, "report_agent")
        self.assertEqual(set(report.depends_on_keys), {task.task_key for task in round.tasks[:-1]})


class TestValidateRound(unittest.TestCase):
    def setUp(self) -> None:
        self.scope, self.budget = scope_fixture(), budget_fixture()
        self.round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate_fixture()], self.scope,
                                self.budget, False, NOW)

    def accept(self, round=None, **overrides):
        round = round or self.round
        return validate_round(round, self.scope, self.budget, [], 0, 0, 0, NOW, **overrides)

    def test_happy_path_accepts(self) -> None:
        decision = self.accept()
        self.assertEqual((decision.outcome, decision.reasons), ("accepted", []))

    def test_out_of_scope_source_blocked(self) -> None:
        task = self.round.tasks[0].model_copy(update={"source_ids": [uuid4()]})
        round = self.round.model_copy(update={"tasks": [task, *self.round.tasks[1:]]})
        decision = self.accept(round)
        self.assertEqual(decision.outcome, "rejected")
        self.assertTrue(any(reason.startswith("source_out_of_scope") for reason in decision.reasons))

    def test_budgets_enforced(self) -> None:
        decision = validate_round(self.round, self.scope, self.budget, [], 64, 0, 0, NOW)
        self.assertIn("call_budget_exhausted", decision.reasons)
        accepted = self.round.model_copy(update={"status": "accepted"})
        second = plan_round(uuid4(), uuid4(), uuid4(), 2, accepted.id, [], [candidate_fixture()],
                            self.scope, budget_fixture(max_rounds=1), False, NOW)
        decision = validate_round(second, self.scope, budget_fixture(max_rounds=1), [accepted], 0, 0, 0, NOW)
        self.assertIn("round_budget_exhausted", decision.reasons)
        decision = validate_round(self.round, self.scope, budget_fixture(max_tasks=1), [], 0, 0, 0, NOW)
        self.assertIn("task_budget_exhausted", decision.reasons)

    def test_cycle_and_lineage_rejected(self) -> None:
        first, second = self.round.tasks[0], self.round.tasks[1]
        cyclic = self.round.model_copy(update={"tasks": [
            first.model_copy(update={"depends_on_keys": [second.task_key]}),
            second.model_copy(update={"depends_on_keys": [first.task_key]})]})
        self.assertIn("round_tasks_contain_a_cycle", self.accept(cyclic).reasons)
        accepted = self.round.model_copy(update={"status": "accepted"})
        decision = validate_round(self.round, self.scope, self.budget, [accepted], 0, 0, 0, NOW)
        self.assertIn("duplicate_round_number", decision.reasons)
        advanced = self.round.model_copy(update={"round_number": 3})
        decision = validate_round(advanced, self.scope, self.budget, [accepted], 0, 0, 0, NOW)
        self.assertIn("round_number_must_advance_lineage_by_one", decision.reasons)

    def test_scope_digest_mismatch_and_bad_roles_rejected(self) -> None:
        other = scope_fixture().model_copy(update={"config_hash": "d" * 64})
        decision = validate_round(self.round, other, self.budget, [], 0, 0, 0, NOW)
        self.assertIn("scope_changed_since_acceptance", decision.reasons)
        task = self.round.tasks[0].model_copy(update={"tool_names": ["email_sender"]})
        round = self.round.model_copy(update={"tasks": [task, *self.round.tasks[1:]]})
        decision = self.accept(round)
        self.assertTrue(any(reason.startswith("tools_not_approved") for reason in decision.reasons))
        bare = self.round.tasks[1].model_copy(update={"candidate_ids": [], "snapshot_ids": []})
        round = self.round.model_copy(update={"tasks": [self.round.tasks[0], bare]})
        decision = self.accept(round)
        self.assertTrue(any(reason.startswith("verifier_without_evidence_refs") for reason in decision.reasons))

    def test_unknown_task_keys_rejected_by_contract(self) -> None:
        bad = RoundTask(task_key="x", role="source_researcher", description="d", depends_on_keys=["missing"])
        with self.assertRaises(SchemaError):
            InvestigationRound(id=uuid4(), run_id=uuid4(), watchlist_id=uuid4(), revision_id=uuid4(),
                round_number=1, tasks=[bad], scope_digest="s" * 64, reserved_calls=1, reserved_tokens=1,
                created_at=NOW)


class TestCoverageAndTermination(unittest.TestCase):
    def test_coverage_tracks_questions_and_candidates(self) -> None:
        candidate = candidate_fixture()
        linked = question_fixture([candidate.id])
        lone = question_fixture()
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [linked], [candidate], scope_fixture(),
                           budget_fixture(), False, NOW)
        accepted = round.model_copy(update={"status": "accepted"})
        entries = {entry.question_id: entry for entry in coverage_status([linked, lone], [accepted])}
        self.assertEqual(entries[linked.id].status, "covered")
        self.assertEqual(entries[linked.id].round_numbers, [1])
        self.assertEqual(entries[lone.id].status, "uncovered")
        done, reason = should_terminate(1, coverage_status([linked], [accepted]), budget_fixture(), 0)
        self.assertEqual((done, reason), (True, "coverage_complete"))

    def test_termination_rules(self) -> None:
        open_question = question_fixture()
        done, reason = should_terminate(1, coverage_status([open_question], []), budget_fixture(), 0)
        self.assertEqual((done, reason), (False, "investigation_continues"))
        accepted_round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate_fixture()],
                                    scope_fixture(), budget_fixture(), False, NOW).model_copy(
                                        update={"status": "accepted"})
        done, reason = should_terminate(1, coverage_status([open_question], [accepted_round]),
                                        budget_fixture(), 0)
        # The question is unreferenced, so coverage stays open and rounds continue.
        self.assertEqual((done, reason), (False, "investigation_continues"))
        done, reason = should_terminate(4, coverage_status([open_question], []), budget_fixture(max_rounds=3), 0)
        self.assertEqual((done, reason), (True, "round_budget_exhausted"))
        done, reason = should_terminate(1, coverage_status([open_question], []), budget_fixture(), 64)
        self.assertEqual((done, reason), (True, "call_budget_exhausted"))
        with self.assertRaises(ValueError):
            plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [], scope_fixture(), budget_fixture(), False,
                       NOW)

    def test_round_fits_budget_headroom_without_consuming(self) -> None:
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate_fixture()], scope_fixture(),
                           budget_fixture(), False, NOW)
        self.assertTrue(round_fits_budget({"calls": 0, "reserved_estimated_tokens": 0, "max_calls": 64,
                                           "max_tokens": 262144}, round))
        self.assertFalse(round_fits_budget({"calls": 64, "reserved_estimated_tokens": 0, "max_calls": 64,
                                            "max_tokens": 262144}, round))


class TestRuntimeTasksAndRecovery(unittest.TestCase):
    def test_to_runtime_tasks_assigns_ids_lineage_and_excerpts(self) -> None:
        candidate = candidate_fixture()
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [question_fixture([candidate.id])],
                           [candidate], scope_fixture(), budget_fixture(), False, NOW)
        tasks = to_runtime_tasks(round, 7, {str(candidate.after_snapshot_id): "excerpt"}, 300)
        self.assertEqual([task.id for task in tasks], list(range(7, 7 + len(tasks))))
        for task in tasks:
            self.assertEqual(task.config["investigation_round_id"], str(round.id))
            self.assertEqual(task.agent_id, task.node)
        verifier = next(task for task in tasks if task.agent_id == "evidence_verifier")
        researcher = next(task for task in tasks if task.agent_id == "source_researcher")
        self.assertEqual(verifier.dependencies, [researcher.id])
        self.assertIn("excerpt", verifier.input_mapping["evidence_excerpts"])

    def test_reconstruct_marks_running_interrupted_without_replay(self) -> None:
        candidate = candidate_fixture()
        round = plan_round(uuid4(), uuid4(), uuid4(), 1, None, [], [candidate], scope_fixture(),
                           budget_fixture(), False, NOW).model_copy(update={"status": "accepted"})
        tasks = to_runtime_tasks(round, 1, {})
        running = [task.model_copy(update={"status": "running"}) for task in tasks]
        round_updates, task_updates = reconstruct_after_restart([round], running)
        self.assertEqual(set(task_updates), {task.id for task in tasks})
        self.assertTrue(all(status == "interrupted" for status, _ in task_updates.values()))
        self.assertEqual(round_updates, {str(round.id): "completed"})
        # Tasks outside accepted rounds and pending work are left alone.
        pending = [task.model_copy(update={"status": "pending"}) for task in tasks]
        round_updates, task_updates = reconstruct_after_restart([round], pending)
        self.assertEqual((round_updates, task_updates), ({}, {}))


class TestDispatcherWithRounds(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_blocks_dependents_while_siblings_survive(self) -> None:
        from app.execution.nodes.dispatcher import TaskDispatcher
        dispatcher = TaskDispatcher()
        plan = [Task(id=1, node="source_researcher", description="a", status="interrupted"),
                Task(id=2, node="product_analyst", description="b", status="pending", dependencies=[1]),
                Task(id=3, node="evidence_verifier", description="c", status="pending")]
        skipped = await dispatcher.dispatch({"plan": plan})
        self.assertEqual({task.id for task in skipped["plan"]}, {2})
        done_plan = [task.model_copy(update={"status": "done"}) if task.id == 3 else task
                     for task in skipped["plan"]]
        waiting = await dispatcher.dispatch({"plan": done_plan})
        self.assertIsNone(waiting["current_task"])
        ended = await dispatcher.dispatch({"plan": [task.model_copy(update={"status": "done"})
                                                   if task.id == 2 else task for task in done_plan]})
        self.assertEqual(ended["mode"], "conversation")


class TestInvestigationService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from app.modules.competitive_intelligence.investigation_service import InvestigationService
        self.rounds, self.snapshots = AsyncMock(), AsyncMock()
        self.service = InvestigationService(self.rounds, self.snapshots)
        self.scope, self.budget = scope_fixture(), budget_fixture()
        self.rounds.list_rounds = AsyncMock(return_value=[])
        self.rounds.save_round = AsyncMock(side_effect=lambda round, owner: round)

    async def test_propose_accepts_and_rejects_visibly(self) -> None:
        candidate = candidate_fixture()
        stored, outcome = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget, 0, 0, 0, False, NOW)
        self.assertEqual((stored.status, outcome), ("accepted", "accepted"))
        self.assertEqual(stored.decided_by, "coordinator_code")
        over = budget_fixture(max_tasks=1)
        stored, outcome = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate, candidate_fixture()], self.scope, over,
            0, 0, 0, False, NOW)
        self.assertEqual((stored.status, outcome), ("rejected", "rejected"))
        self.assertTrue(stored.rejection_reasons)

    async def test_second_round_continues_lineage(self) -> None:
        candidate = candidate_fixture()
        first, _ = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget, 0, 0, 0, False, NOW)
        accepted = first.model_copy(update={"status": "accepted"})
        self.rounds.list_rounds = AsyncMock(return_value=[accepted])
        run_id = first.run_id
        stored, outcome = await self.service.propose_round(
            run_id, uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget, 8, 1000, 2,
            False, NOW)
        self.assertEqual((stored.round_number, stored.parent_round_id, outcome), (2, accepted.id, "accepted"))

    async def test_taken_round_number_rejected_without_persisting_duplicate(self) -> None:
        candidate = candidate_fixture()
        first, _ = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate], self.scope, budget_fixture(max_tasks=1),
            0, 0, 0, False, NOW)
        self.assertEqual(first.status, "rejected")
        self.rounds.list_rounds = AsyncMock(return_value=[first])
        calls_before = self.rounds.save_round.await_count
        stored, outcome = await self.service.propose_round(
            first.run_id, uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget,
            0, 0, 0, False, NOW)
        self.assertEqual((stored.status, outcome), ("rejected", "rejected"))
        self.assertIn("duplicate_round_number", stored.rejection_reasons)
        self.assertEqual(self.rounds.save_round.await_count, calls_before)

    async def test_finalize_requires_terminal_tasks(self) -> None:
        candidate = candidate_fixture()
        stored, _ = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget, 0, 0, 0, False, NOW)
        self.rounds.get_round = AsyncMock(return_value=stored)
        self.rounds.set_round_status = AsyncMock(side_effect=lambda *args: stored.model_copy(
            update={"status": "completed"}))
        tasks = to_runtime_tasks(stored, 1, {})
        with self.assertRaises(ValueError):
            await self.service.finalize_round(str(stored.id), "owner", tasks, NOW)
        done = [task.model_copy(update={"status": "done"}) for task in tasks]
        finalized = await self.service.finalize_round(str(stored.id), "owner", done, NOW)
        self.assertEqual(finalized.status, "completed")

    async def test_reconstruct_applies_without_replay(self) -> None:
        candidate = candidate_fixture()
        stored, _ = await self.service.propose_round(
            uuid4(), uuid4(), uuid4(), "owner", [], [candidate], self.scope, self.budget, 0, 0, 0, False, NOW)
        accepted = stored.model_copy(update={"status": "accepted"})
        self.rounds.list_rounds = AsyncMock(return_value=[accepted])
        self.rounds.set_round_status = AsyncMock(side_effect=lambda *args: accepted)
        tasks = [task.model_copy(update={"status": "running"})
                 for task in to_runtime_tasks(accepted, 1, {})]
        round_updates, task_updates = await self.service.reconstruct(accepted.run_id, "owner", tasks)
        self.assertEqual(round_updates, {str(accepted.id): "completed"})
        self.assertTrue(all(status == "interrupted" for status, _ in task_updates.values()))


class TestCIRolesResolve(unittest.TestCase):
    def test_new_profiles_use_ci_scoped_tools(self) -> None:
        from app.execution.agents.resolver import DEFAULT_AGENT_PROFILES
        from app.modules.competitive_intelligence.service import SAFE_TOOLS
        for role in ("product_analyst", "evidence_verifier", "competitive_analyst"):
            profile = DEFAULT_AGENT_PROFILES[role]
            self.assertEqual(profile.runtime_name, "worker")
            self.assertTrue(set(profile.tool_names) <= SAFE_TOOLS)
            self.assertTrue(set(profile.tool_names) <= set(CI_ROLE_TOOLS[role]))

    def test_ci_workflows_accept_new_roles_but_reject_unknown(self) -> None:
        from app.modules.competitive_intelligence.service import IntelligenceService
        from app.modules.runs.models import RunDocument
        for role in ("product_analyst", "evidence_verifier", "competitive_analyst"):
            document = RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner",
                workflow_version_id="v", status="queued",
                plan=[Task(id=1, node=role, agent_id=role, tool_names=["text_summarizer"],
                           description="Verify", status="pending")],
                resolved_model_config={"agent_profiles": {role: {"name": role}}})
            IntelligenceService.validate_execution(document, self._watchlist())
        bad = RunDocument(run_id=str(uuid4()), flow_id="flow", user_id="owner", workflow_version_id="v",
            status="queued", plan=[Task(id=1, node="debater", agent_id="debater",
                                        tool_names=["text_summarizer"], description="Vote", status="pending")],
            resolved_model_config={"agent_profiles": {"debater": {"name": "debater"}}})
        from app.shared.errors import ValidationError
        with self.assertRaises(ValidationError):
            IntelligenceService.validate_execution(bad, self._watchlist())

    def _watchlist(self):
        from app.modules.competitive_intelligence.models import (
            CreateWatchlist, Revision, Watchlist, WatchlistConfig, digest,
        )
        request = CreateWatchlist(name="W", config=WatchlistConfig(
            goal="Track pricing", dimensions=["pricing"], workflow_version_id="v",
            comparison_criteria=[{"field": "price", "objective": "Assess affordability"}],
            products=[{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                      {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                       "sources": [{"id": SOURCE_A, "url": "https://rival.invalid/pricing", "kind": "pricing"}]}]))
        now = NOW
        return Watchlist(id=uuid4(), owner_id="owner", name="W", description="", status="active",
            created_at=now, updated_at=now, current_revision=Revision(
                id=uuid4(), watchlist_id=uuid4(), revision_number=1, config=request.config,
                config_hash=digest(request.config), approval_status="approved", created_at=now))

    def test_migration_chain_links_0007_to_0008(self) -> None:
        import importlib.util
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic",
                                             "versions", "0008_ci_investigation.py"))
        spec = importlib.util.spec_from_file_location("migration_0008", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0007_ci_snapshots")
        self.assertEqual(module.revision, "0008_ci_investigation")


if __name__ == "__main__":
    unittest.main()
