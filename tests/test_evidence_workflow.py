"""Three-agent hand-off with mocked collection/inference and a real sandboxed report renderer."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.execution.agents.base import WorkerAgent
from app.execution.research_contracts import ChunkExtraction, ExtractedClaim, SynthesisFinding, SynthesisResult
from app.execution.state import Task
from app.execution.tools.contracts import SourceMetadata, success_result
from app.execution.tools.markdown_report_generator_tool import markdown_report_generator
from test_support import isolated_workspace


class EvidenceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_synthesis_report_preserves_numeric_evidence_and_citation(self) -> None:
        excerpt = "Model Alpha scored 62.2% on Benchmark X, setup A."
        url = "https://fixture.invalid/paper"

        @tool("news_crawler")
        def crawl(url: str) -> str:
            """Return a mocked source document without network access."""
            return success_result({"text": excerpt}, source=SourceMetadata(final_url=url)).to_json()

        store = MagicMock()
        store.check_active = AsyncMock()
        store.save_document = AsyncMock()
        store.save_bundle = AsyncMock()
        mapper = MagicMock()
        mapper.model = "mock-model"
        mapper.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim=excerpt, excerpt=excerpt, subject="Model Alpha", metric="Benchmark X",
                           value_text="62.2", unit="%", evaluation_setup="setup A")]))
        mapper.bind_tools.return_value.ainvoke = AsyncMock(side_effect=[
            AIMessage(content="", tool_calls=[{"name": "news_crawler", "args": {"url": url}, "id": "crawl1"}]),
            AIMessage(content="Collected evidence")])
        source = WorkerAgent("source_researcher", "Collect evidence", mapper, [crawl])
        source.evidence_store = store
        source_output = await source.execute({"current_task": Task(id=1, node="source_researcher",
            status="pending", description="Collect benchmark evidence"), "metadata": {"run_id": "fixture"}})
        self.assertEqual(source_output["current_task"].status, "done")
        source_record = source_output["result_storage"][0]
        identity = source_record["result"]["claims"][0]["evidence_id"]

        reducer = MagicMock()
        reducer.model = "mock-model"
        reducer.with_structured_output.return_value.ainvoke = AsyncMock(return_value=SynthesisResult(findings=[
            SynthesisFinding(text=excerpt, evidence_ids=[identity])]))
        synthesis = WorkerAgent("synthesis_agent", "Reconcile", reducer)
        synthesis.evidence_store = store
        synthesized = await synthesis.execute({"current_task": Task(id=2, node="synthesis_agent", status="pending",
            description="Reconcile scores", dependencies=[1]), "metadata": {"run_id": "fixture"},
            "result_storage": [source_record]})
        self.assertEqual(synthesized["current_task"].status, "done")

        writer = MagicMock()
        writer.bind_tools.return_value.ainvoke = AsyncMock(side_effect=[AIMessage(content="", tool_calls=[{
            "id": "report1", "name": "markdown_report_generator", "args": {"title": "Fixture report",
                "sections": [{"header": "Finding", "content": f"{excerpt} [Source]({url})"}],
                "filename": "fixture-report.md"}}]), AIMessage(content="Report saved")])
        reporter = WorkerAgent("report_agent", "Write a sourced report", writer, [markdown_report_generator])
        reporter.evidence_store = store
        with isolated_workspace() as root:
            report = await reporter.execute({"current_task": Task(id=3, node="report_agent", status="pending",
                description="Publish report", dependencies=[2]), "result_storage": synthesized["result_storage"]})
            self.assertEqual(report["current_task"].status, "done")
            path = Path(report["result_storage"][0]["artifact_paths"][0])
            self.assertIn(root, path.parents)
            content = path.read_text(encoding="utf-8")
            self.assertIn("62.2%", content)
            self.assertIn(url, content)
