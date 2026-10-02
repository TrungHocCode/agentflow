"""Synthesis lineage and typed hand-off tests with deterministic model outputs."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.config import settings
from app.execution.agents.base import WorkerAgent
from app.execution.research_contracts import EvidenceClaim, ResearchResult, SynthesisFinding, SynthesisResult
from app.execution.research_reduction import EvidenceReducer
from app.execution.state import Task


class EvidenceReductionTests(unittest.IsolatedAsyncioTestCase):
    def claim(self, identity: str = "e1") -> EvidenceClaim:
        return EvidenceClaim(evidence_id=identity, document_id="d1", chunk_id="c1",
                             source_url="https://example.org/paper", start_offset=0, end_offset=12,
                             claim="Score: 62.2%", excerpt="Score: 62.2%", value_text="62.2")

    def fixture(self, identities: list[str]) -> tuple[MagicMock, MagicMock]:
        model = MagicMock()
        model.model = "mock-model"
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=SynthesisResult(
            findings=[SynthesisFinding(text="Score is 62.2%; other conditions are unknown.", evidence_ids=identities)],
            limitations=["Evaluation setup missing"]))
        store = MagicMock()
        store.check_active = AsyncMock()
        return model, store

    async def test_synthesis_retains_source_lineage_and_limitations(self) -> None:
        model, store = self.fixture(["e1"])
        worker = WorkerAgent("synthesis_agent", "Reconcile evidence", model)
        worker.evidence_store = store
        result = await worker.execute({"current_task": Task(id=2, node="synthesis_agent", status="pending",
            description="Compare benchmarks", dependencies=[1]), "metadata": {"run_id": "run"},
            "result_storage": [{"task_id": 1, "result": ResearchResult(claims=[self.claim()],
                status="partial", warnings=["Missing second model"]).model_dump()}]})
        content = result["result_storage"][0]["result"]
        self.assertEqual(result["current_task"].status, "partial")
        self.assertEqual(content["sources"]["e1"], "https://example.org/paper")
        self.assertIn("Missing second model", content["limitations"])
        model.bind_tools.assert_not_called()

    async def test_unknown_evidence_reference_is_rejected(self) -> None:
        model, store = self.fixture(["invented"])
        with self.assertRaises(ValueError):
            await EvidenceReducer(model, store, "run", 2).reduce([self.claim()], "Compare")

    async def test_empty_evidence_does_not_generate_unsupported_report_input(self) -> None:
        model, store = self.fixture(["e1"])
        with self.assertRaises(ValueError):
            await EvidenceReducer(model, store, "run", 2).reduce([], "Compare")
        model.with_structured_output.assert_not_called()

    async def test_reduction_call_budget_is_enforced(self) -> None:
        model, store = self.fixture(["e1"])
        with patch.object(settings, "RESEARCH_MAX_REDUCE_CALLS", 0), self.assertRaises(ValueError):
            await EvidenceReducer(model, store, "run", 2).reduce([self.claim()], "Compare")

    async def test_reduction_cannot_invent_a_numeric_score(self) -> None:
        model, store = self.fixture(["e1"])
        model.with_structured_output.return_value.ainvoke.return_value = SynthesisResult(findings=[
            SynthesisFinding(text="The model scored 99.9%", evidence_ids=["e1"])])
        with self.assertRaises(ValueError):
            await EvidenceReducer(model, store, "run", 2).reduce([self.claim()], "Compare")

    async def test_report_does_not_write_an_uncollected_source_url(self) -> None:
        @tool
        def report(content: str) -> str:
            """Mock report renderer which must never run for an invented citation."""
            raise AssertionError("Renderer was called with an unsupported citation")

        model = MagicMock()
        model.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="", tool_calls=[
            {"id": "report1", "name": "report", "args": {"content": "Source https://invented.example/paper"}}]))
        worker = WorkerAgent("report_agent", "Write a report", model, [report])
        worker.evidence_store = MagicMock()
        output = await worker.execute({"current_task": Task(id=3, node="report_agent", status="pending",
            description="Report", dependencies=[2]), "result_storage": [{"task_id": 2, "result": {
                "sources": {"e1": "https://example.org/paper"}, "findings": []}}]})
        self.assertEqual(output["current_task"].status, "failed")
