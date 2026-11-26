"""Supervisor decisions at CI run decision points; mocked LLM, no network."""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
sys.path.insert(0, os.path.dirname(__file__))

from pydantic import ValidationError  # noqa: E402

from app.execution.state import Task  # noqa: E402
from app.modules.competitive_intelligence.investigation_service import (  # noqa: E402
    COORDINATOR_IDENTITY, InvestigationService,
)
from app.modules.competitive_intelligence.loop_hooks import CILoopHooks  # noqa: E402
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate  # noqa: E402
from app.modules.competitive_intelligence.supervisor_decision import (  # noqa: E402
    Decision, SupervisorDecider, build_decision, is_valid, render_state,
)
from test_ci_loop import PRICING, frozen_fixture, researcher_task, run_fixture  # noqa: E402


def raw_state(statuses: list[str], budget: int = 10, analyzed: tuple[str, ...] = ()) -> dict:
    sources = [{"id": f"S{i}", "product": "Rival", "dimension": "pricing", "status": status,
                "analyzed": f"S{i}" in analyzed, **({"change": "Before: $5\nAfter: $7"} if status == "changed" else {})}
               for i, status in enumerate(statuses, 1)]
    return {"goal": "Track pricing", "round": 1, "budget_calls_left": budget, "sources": sources}


def llm_returning(**fields):
    """Stub planner model: with_structured_output(schema).ainvoke returns schema(**fields)."""
    llm = MagicMock()

    def structured(schema):
        model = MagicMock()
        model.ainvoke = AsyncMock(return_value=schema(**fields))
        return model

    llm.with_structured_output.side_effect = structured
    return llm


class TestRenderState(unittest.TestCase):
    def test_first_capture_mapping_and_no_decision(self) -> None:
        rendered = render_state(raw_state(["baseline_created", "unchanged"]))
        self.assertEqual([item["status"] for item in rendered["sources"]], ["first_capture", "unchanged"])
        self.assertTrue(all(item["allowed_actions"] == [] for item in rendered["sources"]))

    def test_changed_source_allows_analysis_once_and_budget_closes_it(self) -> None:
        self.assertEqual(render_state(raw_state(["changed"]))["sources"][0]["allowed_actions"], ["analyze_change"])
        self.assertEqual(render_state(raw_state(["changed"], analyzed=("S1",)))["sources"][0]["allowed_actions"], [])
        self.assertEqual(render_state(raw_state(["changed"], budget=0))["sources"][0]["allowed_actions"], [])

    def test_unoffered_actions_never_appear(self) -> None:
        schema = build_decision(render_state(raw_state(["changed", "unchanged"])))
        actions = set(schema.model_fields["action"].annotation.__args__)
        self.assertEqual(actions, {"analyze_change", "ask_user", "finish"})


class TestPerCallSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.rendered = render_state(raw_state(["changed", "unchanged", "changed"], budget=1))
        self.schema = build_decision(self.rendered)

    def test_accepts_allowed_action_and_id(self) -> None:
        self.assertEqual(self.schema(situation="s", action="analyze_change", targets=["S1"], reason="r").targets,
                         ["S1"])

    def test_rejects_disallowed_action_id_and_over_budget(self) -> None:
        for fields in ({"action": "find_source", "targets": []}, {"action": "retry_fetch", "targets": []},
                       {"action": "analyze_change", "targets": ["S2"]},
                       {"action": "analyze_change", "targets": ["S1", "S3"]}):
            with self.assertRaises(ValidationError):
                self.schema(situation="s", reason="r", **fields)

    def test_code_validation_rejects_targets_on_finish_and_empty_analysis(self) -> None:
        self.assertFalse(is_valid(Decision(situation="s", action="finish", targets=["S1"], reason="r"), self.rendered))
        self.assertFalse(is_valid(Decision(situation="s", action="analyze_change", targets=[], reason="r"),
                                  self.rendered))
        self.assertTrue(is_valid(Decision(situation="s", action="analyze_change", targets=["S3"], reason="r"),
                                 self.rendered))


class TestDecider(unittest.IsolatedAsyncioTestCase):
    async def test_no_decision_state_makes_no_model_call(self) -> None:
        factory = MagicMock()
        self.assertIsNone(await SupervisorDecider(factory).decide(raw_state(["baseline_created"])))
        factory.assert_not_called()

    async def test_valid_decision_returned(self) -> None:
        decider = SupervisorDecider(lambda: llm_returning(situation="s", action="analyze_change",
                                                          targets=["S1"], reason="price rose"))
        decision = await decider.decide(raw_state(["changed"]))
        self.assertEqual((decision.action, decision.targets), ("analyze_change", ["S1"]))

    async def test_model_error_and_timeout_and_bad_output_fall_back(self) -> None:
        failing = MagicMock()
        failing.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        self.assertIsNone(await SupervisorDecider(lambda: failing).decide(raw_state(["changed"])))

        async def hang(_messages):
            await asyncio.sleep(5)

        slow = MagicMock()
        slow.with_structured_output.return_value.ainvoke = hang
        self.assertIsNone(await SupervisorDecider(lambda: slow, timeout_seconds=0.01).decide(raw_state(["changed"])))
        # A model that ignores the schema and returns a disallowed action is rejected, not executed.
        bad = MagicMock()
        bad.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=SimpleNamespace(situation="s", action="find_source", targets=[], reason="r"))
        self.assertIsNone(await SupervisorDecider(lambda: bad).decide(raw_state(["changed"])))


def candidate(source_id) -> ChangeCandidate:
    return ChangeCandidate(id=uuid4(), run_id=uuid4(), source_id=source_id, before_snapshot_id=uuid4(),
                           after_snapshot_id=uuid4(), kind="modified", section="Pricing", before_excerpt="$5",
                           after_excerpt="$7", diff_algorithm_version="v1", diff_hash="a" * 64,
                           detected_at=datetime.now(timezone.utc))


class TestHookDecisionPoints(unittest.IsolatedAsyncioTestCase):
    def build(self, decider):
        hooks = CILoopHooks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), MagicMock(), decider=decider)
        frozen = frozen_fixture()
        self.source_id = next(iter(frozen["baselines"]))
        self.run = run_fixture(frozen, [researcher_task()])
        record = MagicMock(task_id=1, metadata={"kind": "source_document"},
                           content={"source_url": "https://rival.invalid/pricing", "storage_uri": "u"})
        hooks.research.list_results = AsyncMock(return_value=[record])
        hooks.artifacts.resolve.return_value.read_text.return_value = PRICING
        hooks.snapshots.capture = AsyncMock(return_value=(MagicMock(), MagicMock(id=uuid4())))
        self.changed = candidate(self.source_id)
        hooks.snapshots.compare_source = AsyncMock(return_value=(MagicMock(outcome="changed"), [self.changed]))
        hooks.snapshots.finalize_source = AsyncMock()
        hooks.snapshots.list_comparisons = AsyncMock(return_value=[
            SimpleNamespace(source_id=self.source_id, outcome="changed")])
        hooks.investigations.list_rounds = AsyncMock(return_value=[])
        self.followup = Task(id=9, node="competitive_analyst", description="Analyze", status="pending")
        hooks.investigations.propose_round = AsyncMock(return_value=(MagicMock(), "accepted"))
        hooks.investigations.materialize_tasks = AsyncMock(return_value=[self.followup])
        return hooks

    def decider_returning(self, decision):
        decider = MagicMock()
        decider.decide = AsyncMock(return_value=decision)
        return decider

    async def test_flag_off_uses_default_planner_without_supervisor(self) -> None:
        hooks = self.build(None)
        self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [self.followup])
        self.assertEqual(hooks.investigations.propose_round.call_args.args[-1], COORDINATOR_IDENTITY)
        self.assertNotIn("supervisor", self.run.metadata)

    async def test_valid_decision_runs_supervisor_round_and_is_audited(self) -> None:
        decision = Decision(situation="price rose", action="analyze_change", targets=["S1"], reason="matters")
        hooks = self.build(self.decider_returning(decision))
        self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [self.followup])
        args = hooks.investigations.propose_round.call_args.args
        self.assertEqual(args[-1], "supervisor")
        self.assertEqual([c.id for c in args[5]], [self.changed.id])
        raw = hooks.decider.decide.call_args.args[0]
        self.assertEqual(raw["sources"][0]["status"], "changed")
        self.assertLessEqual(len(raw["sources"][0]["change"]), 600)
        self.assertEqual(self.run.metadata["supervisor"]["decisions"][0]["reason"], "matters")

    async def test_finish_and_ask_user_create_no_round(self) -> None:
        for action, expect_question in (("finish", False), ("ask_user", True)):
            decision = Decision(situation="s", action=action, targets=[], reason="x" * 400)
            hooks = self.build(self.decider_returning(decision))
            self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [])
            hooks.investigations.propose_round.assert_not_awaited()
            questions = self.run.metadata["supervisor"].get("pending_questions", [])
            self.assertEqual(len(questions), int(expect_question))
            self.assertTrue(all(len(question) <= 300 for question in questions))

    async def test_no_decision_falls_back_to_default_plan(self) -> None:
        hooks = self.build(self.decider_returning(None))
        self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [self.followup])
        self.assertEqual(hooks.investigations.propose_round.call_args.args[-1], COORDINATOR_IDENTITY)

    async def test_decider_exception_falls_back_to_default_plan(self) -> None:
        decider = MagicMock()
        decider.decide = AsyncMock(side_effect=RuntimeError("boom"))
        hooks = self.build(decider)
        self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [self.followup])
        self.assertEqual(hooks.investigations.propose_round.call_args.args[-1], COORDINATOR_IDENTITY)

    async def test_rejected_supervisor_round_falls_back_to_default(self) -> None:
        decision = Decision(situation="s", action="analyze_change", targets=["S1"], reason="r")
        hooks = self.build(self.decider_returning(decision))
        hooks.investigations.propose_round = AsyncMock(side_effect=[(MagicMock(), "rejected"),
                                                                    (MagicMock(), "accepted")])
        self.assertEqual(await hooks.on_task_completed(self.run, researcher_task(), "done"), [self.followup])
        planners = [call.args[-1]
                    for call in hooks.investigations.propose_round.await_args_list]
        self.assertEqual(planners, ["supervisor", COORDINATOR_IDENTITY])

    async def test_end_of_round_decides_once_then_stops(self) -> None:
        decision = Decision(situation="s", action="analyze_change", targets=["S1"], reason="r")
        hooks = self.build(self.decider_returning(decision))
        hooks.snapshots.list_candidates = AsyncMock(return_value=[self.changed])
        scope = hooks.ci_scope(self.run)
        round_task = Task(id=2, node="competitive_analyst", description="a", status="done",
                          config={"investigation_round_id": "r1"})
        self.run.plan = [round_task]
        self.assertEqual(await hooks.on_task_completed(self.run, round_task, "done"), [self.followup])
        self.assertEqual(hooks.investigations.propose_round.call_args.args[-1], "supervisor")
        self.assertEqual(await hooks.on_task_completed(self.run, round_task, "done"), [])  # once per round
        self.assertEqual(hooks.investigations.propose_round.await_count, 1)
        self.assertIsNotNone(scope)

    async def test_end_of_round_failure_stops_instead_of_replanning_analyzed_changes(self) -> None:
        hooks = self.build(self.decider_returning(None))
        hooks.snapshots.list_candidates = AsyncMock(return_value=[self.changed])
        round_task = Task(id=2, node="competitive_analyst", description="a", status="done",
                          config={"investigation_round_id": "r1"})
        self.run.plan = [round_task]
        self.assertEqual(await hooks.on_task_completed(self.run, round_task, "done"), [])
        hooks.investigations.propose_round.assert_not_awaited()

    async def test_budget_exhausted_blocks_end_of_round_decision(self) -> None:
        hooks = self.build(self.decider_returning(None))
        hooks.snapshots.list_candidates = AsyncMock(return_value=[self.changed])
        self.run.metadata["run_budget"] = {"calls": 10_000}
        round_task = Task(id=2, node="competitive_analyst", description="a", status="done",
                          config={"investigation_round_id": "r1"})
        self.run.plan = [round_task]
        self.assertEqual(await hooks.on_task_completed(self.run, round_task, "done"), [])
        hooks.decider.decide.assert_not_awaited()


class TestProposeRoundPlanner(unittest.IsolatedAsyncioTestCase):
    async def test_planner_recorded_and_rejected_supervisor_round_not_persisted(self) -> None:
        from app.modules.competitive_intelligence.investigation_coordinator import plan_round  # noqa: F401
        rounds = MagicMock()
        rounds.list_rounds = AsyncMock(return_value=[])
        rounds.save_round = AsyncMock(side_effect=lambda round, owner: round)
        service = InvestigationService(rounds, MagicMock())
        frozen = frozen_fixture()
        hooks_scope = CILoopHooks._round_scope(frozen)
        source_id = next(iter(frozen["baselines"]))
        found = candidate(source_id)
        from uuid import UUID
        from app.modules.competitive_intelligence.models import InvestigationBudget
        questions = CILoopHooks._questions(frozen, UUID(frozen["watchlist_id"]), [found])
        args = (uuid4(), UUID(frozen["watchlist_id"]), UUID(frozen["revision_id"]), "owner", questions, [found],
                hooks_scope, InvestigationBudget(), 0, 0, 0, False, datetime.now(timezone.utc))
        stored, outcome = await service.propose_round(*args, "supervisor")
        self.assertEqual((outcome, stored.decided_by), ("accepted", "supervisor"))
        tiny = InvestigationBudget(max_llm_calls=1, max_total_tokens=1)
        rounds.save_round.reset_mock()
        _, outcome = await service.propose_round(*args[:7], tiny, 0, 0, 0, False, args[-1], "supervisor")
        self.assertEqual(outcome, "rejected")
        rounds.save_round.assert_not_awaited()
        _, outcome = await service.propose_round(*args[:7], tiny, 0, 0, 0, False, args[-1])
        self.assertEqual(outcome, "rejected")
        rounds.save_round.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
