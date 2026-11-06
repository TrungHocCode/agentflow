import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from app.execution.agents.base import ROUND_EVIDENCE_MAX_CHARS, WorkerAgent, _round_context
from app.execution.state import Task
from app.modules.competitive_intelligence.brief_contracts import BriefSourceCoverage, IntelligenceBrief
from app.modules.competitive_intelligence.brief_render import render_markdown
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.investigation_contracts import InvestigationRound, RoundTask
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.runs.models import RunDocument

SOURCE_ID = uuid4()
CONFIG = {
    "products": [
        {"name": "NoteNest", "kind": "own", "description": "Notes for teams",
         "profile": {"target_segments": ["SMB"], "facts": [{"field": "price", "value": "$4"}]},
         "sources": []},
        {"name": "Notion", "kind": "competitor", "profile": {},
         "sources": [{"id": str(SOURCE_ID), "url": "https://notion.com/pricing", "kind": "release_notes"}]},
    ],
    "comparison_criteria": [{"field": "price", "objective": "Affordability"}],
}


def state(task: Task) -> dict:
    return {"current_task": task, "messages": [], "metadata": {
        "input_data": {"competitive_intelligence": {"config": CONFIG}}}}


def round_task(**updates) -> Task:
    values = {"id": 2, "node": "competitive_analyst", "status": "running", "description": "Compare",
              "config": {"investigation_round_id": "r1"},
              "input_mapping": {"evidence_excerpts": ["Before: $5\nAfter: $7"]}}
    return Task(**{**values, **updates})


class TestRoundContext(unittest.TestCase):
    def test_gives_evidence_and_comparison_context(self) -> None:
        context = _round_context(state(round_task()), round_task())
        self.assertIn("Before: $5", context)
        self.assertIn("NoteNest", context)
        self.assertIn("SMB", context)
        self.assertIn("Affordability", context)

    def test_collected_text_is_wrapped_as_untrusted_and_competitors_are_left_out(self) -> None:
        context = _round_context(state(round_task()), round_task())
        self.assertLess(context.index("<tool_output>"), context.index("Before: $5"))
        self.assertLess(context.index("Before: $5"), context.index("</tool_output>"))
        self.assertNotIn("Notion", context)

    def test_evidence_is_capped_to_protect_the_context_budget(self) -> None:
        task = round_task(input_mapping={"evidence_excerpts": ["x" * (ROUND_EVIDENCE_MAX_CHARS * 2)]})
        self.assertLessEqual(_round_context(state(task), task).count("x"), ROUND_EVIDENCE_MAX_CHARS + 2)

    def test_ordinary_tasks_get_nothing_extra(self) -> None:
        task = Task(id=1, node="source_researcher", status="running", description="Collect")
        self.assertEqual(_round_context(state(task), task), "")


class TestWorkerPromptIncludesRoundContext(unittest.IsolatedAsyncioTestCase):
    async def test_analyst_prompt_contains_the_change(self) -> None:
        llm = MagicMock(spec=BaseChatModel)
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="Notion raised prices."))
        agent = WorkerAgent(name="competitive_analyst", system_prompt="Compare.", llm=llm)

        await agent.execute(state(round_task()))

        system_prompt = llm.ainvoke.await_args.args[0][0].content
        self.assertIn("Before: $5", system_prompt)
        self.assertIn("Affordability", system_prompt)


class TestMaterializeTasks(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_supply_their_own_before_and_after_text(self) -> None:
        candidate = MagicMock(id=uuid4(), section="Pricing", kind="modified",
                              before_excerpt="$5", after_excerpt="$7")
        snapshots = AsyncMock()
        snapshots.list_candidates.return_value = [candidate]
        snapshots.read_snapshot_span.side_effect = ValueError("not a snapshot id")
        now = datetime.now(timezone.utc)
        planned = InvestigationRound(
            id=uuid4(), run_id=uuid4(), watchlist_id=uuid4(), revision_id=uuid4(), round_number=1,
            scope_digest="d" * 64, reserved_calls=4, reserved_tokens=4096, created_at=now,
            tasks=[RoundTask(task_key="a", role="competitive_analyst", description="Compare",
                             candidate_ids=[candidate.id], tool_names=["text_summarizer"])])

        tasks = await InvestigationService(AsyncMock(), snapshots).materialize_tasks(planned, 5, "owner")

        self.assertEqual(snapshots.list_candidates.await_args.args, (str(planned.run_id), "owner"))
        excerpt = tasks[0].input_mapping["evidence_excerpts"][0]
        self.assertIn("Before: $5", excerpt)
        self.assertIn("After: $7", excerpt)


class TestBriefUsesAnalysisAndNames(unittest.TestCase):
    @staticmethod
    def run_document(**updates) -> RunDocument:
        values = {"run_id": str(uuid4()), "flow_id": "f", "user_id": "owner", "status": "completed",
                  "input_data": {"competitive_intelligence": {"config": CONFIG}}}
        return RunDocument(**{**values, **updates})

    def test_sources_are_named_from_the_frozen_configuration(self) -> None:
        self.assertEqual(BriefService._source_labels(self.run_document()),
                         {str(SOURCE_ID): "Notion (release notes)"})

    def test_analyst_output_is_kept_and_never_labelled_verified(self) -> None:
        analyst = Task(id=2, node="competitive_analyst", status="done", description="Compare")
        verifier = Task(id=3, node="evidence_verifier", status="done", description="Verify")
        failed = Task(id=4, node="competitive_analyst", status="failed", description="Compare")
        run = self.run_document(plan=[analyst, verifier, failed], result_storage=[
            {"task_id": 2, "result": "Notion raised prices above ours."},
            {"task_id": 3, "result": "Verifier text."},
            {"task_id": 4, "result": "Should be ignored."},
        ])
        findings = BriefService._analyst_findings(run)
        self.assertEqual([finding.text for finding in findings], ["Analysis: Notion raised prices above ours."])
        self.assertEqual(findings[0].verification, "unresolved")

    @staticmethod
    def brief(**coverage) -> IntelligenceBrief:
        now = datetime.now(timezone.utc)
        return IntelligenceBrief(
            id=uuid4(), watchlist_id=uuid4(), revision_id=uuid4(), run_id=uuid4(), outcome="no_change",
            quality="complete", summary="s", artifact_id="a", artifact_uri="", artifact_hash="0" * 64,
            observed_from=now, observed_to=now, created_at=now,
            source_coverage=[BriefSourceCoverage(source_id=SOURCE_ID, outcome="no_change", quality="complete",
                                                 observed_at=now, **coverage)])

    def test_markdown_prefers_the_name(self) -> None:
        self.assertIn("| Notion (pricing) |", render_markdown(self.brief(source_label="Notion (pricing)")))
        self.assertIn(f"| {SOURCE_ID} |", render_markdown(self.brief()))

    def test_label_does_not_change_the_content_hash_of_earlier_briefs(self) -> None:
        brief = self.brief()
        relabelled = brief.model_copy(deep=True)
        relabelled.source_coverage[0].source_label = "Notion (pricing)"
        self.assertEqual(BriefService.content_hash(brief), BriefService.content_hash(relabelled))


if __name__ == "__main__":
    unittest.main()
